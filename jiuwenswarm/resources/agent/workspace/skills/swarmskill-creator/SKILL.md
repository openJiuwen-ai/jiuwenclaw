---
name: swarmskill-creator
description: |
  Creates, converts, or modifies Swarm Skills — the multi-role extension of the Skills standard (specialized roles + workflow + bind constraints).
  Use when the user wants to author a multi-role agent team, convert a single-agent skill into one, or refactor an existing team.
  Do NOT use for single-agent Skills — use create-skill instead.
version: "0.2"
---

# Swarm Skill Creator

Authoring tool for Swarm Skills — the multi-role extension of the Anthropic Skills standard. Encodes the Swarm Skill spec into a repeatable workflow with templates, decision trees, and an automated validator.

## Workflow

This skill has **three modes**. Pick one based on the user's request, then follow the matching pipeline:

| Mode | Trigger | Output |
|---|---|---|
| **CREATE** | User has a fresh need ("build a team for X") | New `<swarmskill-name>/` directory with the full 5-file set |
| **CONVERT** | User points at an existing single-agent skill | Transformed `<swarmskill-name>/` directory + a delta report explaining what the team adds |
| **MODIFY** | User edits an existing Swarm Skill (add/remove role, change workflow, adjust bind, fix validator errors) | Updated files in the existing `<swarmskill-name>/` directory |

> **All three modes share Stages 2–6**. CONVERT differs only in Stage 1 (decomposition replaces fresh design). MODIFY skips Stage 0 and Stage 1 (justification and pattern are already settled — re-open them only if the change alters the pattern, e.g. adding a parallel role to a pipeline team), touches only the affected stages in 2–5, and **MUST run Stage 6 (validator) — this is the most-skipped, most-critical step in MODIFY mode**. See the [MODIFY impact matrix](#modify-impact-matrix) for which stages to re-run per change type.

### Stage 0: Triage — is a Swarm Skill even justified?

A Swarm Skill is **only justified** when at least one of these is true:

1. **Adversarial blind spot** — a single agent role-playing N personas produces converging outputs because it cannot escape its own analytical priors. Examples: PR review, security audit, design critique.
2. **Parallel decomposition gain** — N independent sub-tasks can run concurrently and the integration is non-trivial. Examples: multi-angle research, multi-perspective due diligence.
3. **Specialization pipeline with hard handoffs** — sequential stages with quality gates where blurring stage boundaries causes regressions. Examples: marketing copy (brief → draft → edit → audit), incident response (declare → triage → mitigate → postmortem).

**If none apply → STOP. Recommend a single-agent skill instead.** Swarm Skills cost more tokens, more wall-clock, and more authoring overhead. Do not build one out of novelty.

Ask the user: *"Which of these failure modes does a single agent hit on this task today?"* If they cannot answer, the Swarm Skill is premature.

### Stage 1a (CREATE only): Pattern selection

Pick the architectural pattern (A / B / C / mixed) based on Stage 0 justification. The detailed decision tree with worked examples is in [reference/pattern-selection.md](reference/pattern-selection.md). Quick reference:

| Pattern | When | Role count | Roles see each other? |
|---|---|---|---|
| **A. Adversarial / Cross-check** | Justification #1 (blind spot) | 2–4 | No — isolation is the value |
| **B. Parallel decomposition** | Justification #2 (independent sub-tasks) | 2–N (variable) | No until integration |
| **C. Specialization pipeline** | Justification #3 (sequential expert stages) | 3–5 | Each stage sees prior stage output |
| **Mixed (A+B / B+C / C+A)** | Multiple justifications stack | 4–6 | Pattern-by-stage |
| **Debate (Mixed B+A+C)** | #1 + #2 (blind spot + parallel breadth) | 3–N+1 | Phase-scoped: isolated → mutual |

Read [reference/pattern-selection.md](reference/pattern-selection.md) before committing — picking the wrong pattern is the most common authoring error.

### Stage 1b (CONVERT only): Decompose the existing skill

Read the source SKILL.md and identify natural role boundaries. Use this checklist:

1. **Find embedded personas** — does the skill ask one agent to "act as" multiple roles (e.g., "Persona 1: Saboteur / Persona 2: New Hire / Persona 3: Security Auditor")? Each embedded persona is a Swarm Skill role candidate.
2. **Find sequential stages with quality gates** — does the skill have phases like "first do X, then validate Y, then produce Z"? Each phase is a pipeline stage candidate.
3. **Find checklists that branch by category** — a checklist like "[ ] security  [ ] performance  [ ] readability" is parallel-decomposition fuel.
4. **Identify what's lost in single-agent form** — the answer becomes the Swarm Skill's "why" (the Stage 0 justification). If you cannot articulate this, the conversion is not worth it.

Full conversion methodology: [reference/conversion-guide.md](reference/conversion-guide.md).

### Stage 2: Role design (count depends on pattern, no overlap)

Default role counts by pattern (soft heuristics, not hard limits — see [reference/pattern-selection.md](reference/pattern-selection.md) Q4):

- **A. Adversarial / Cross-check**: 2–4 roles
- **B. Parallel decomposition**: 2–N roles (use `count: [min, max]` for elastic fan-out)
- **C. Specialization pipeline**: 3–5 stages
- **Mixed (A+B / A+C)**: 4–6 roles total

If you need more, prefer splitting into multiple sequential Swarm Skills over one mega-swarm.

For each role, write 5 mandatory sections:

1. `## Identity` — **first line MUST be a 1-line motto** that crystallizes the POV (e.g., *"I am trying to break this code in production."*). This is the #1 anti-convergence mechanism. 0–2 paragraphs of context after.
2. `## Success Criteria` — bullet list + a "Focus areas" line.
3. `## Boundary` — explicit `**Forbidden**:` (prevent role overlap) + `**Mandatory**:` (prevent laziness).
4. `## Output Schema` — Markdown or JSON template.
5. `## Inline Persona for Teammate` — full pasteable prompt (most adopting agents do NOT auto-load role files; the Leader must inline this into the dispatch prompt).

Detailed authoring guide with examples: [reference/role-design.md](reference/role-design.md).

**Anti-overlap test**: write each role's 1-line motto, then ask *"could one role's deliverable substitute for another's?"* If yes, the boundaries are blurred — redesign before proceeding.

**Output**: write each role as `roles/<id>.md` using [templates/role.md.template](templates/role.md.template) as the starting skeleton. Delete the `<!-- TEMPLATE NOTES -->` block before finalizing.

**Auto-matching: assign locally available skills and tools**

After all role files are written, automatically match each role with locally available skills and tools. This is a **silent sub-step** — no user interaction needed (already-installed skills have zero adoption cost).

1. **Scan local skills** — use whatever mechanism the host framework provides. Common approaches (non-exhaustive):
   - **System prompt / context injection**: many frameworks inject an `available_skills` list at startup — this is the most reliable source.
   - **Workspace-level convention scan**: glob for `**/SKILL.md` under known skill directories.
   - **CLI query**: if the framework provides a skill listing command (e.g., `npx skills list -g`), use it.
   - **Do NOT hardcode paths for a specific framework** — the Swarm Skill spec is framework-agnostic.
2. **Scan local tools** — probe CLI tools available in the current environment (e.g., `gh`, `python`, `rg`, `jq`, `curl`, `docker`). Use `where` (Windows) or `which` (Unix).
3. **Match and assign** — for each role, read its `purpose`, `Success Criteria`, and `Boundary`. Evaluate whether any discovered skill/tool would materially help fulfil the role's purpose (a match = removing it would force the role into a significantly weaker operating mode). Assign matches to the correct field with `source: local` in `dependencies.yaml`.

   **Classification rule — do NOT mix skills and tools:**
   - `roles[].skills` → **Skills only**: items that have a `SKILL.md` (agent skills providing instructions/workflows). Examples: `web-research`, `code-review`, `canvas-design`.
   - `roles[].tools` → **Tools only**: CLI executables, MCP servers, or shell commands. Examples: `gh`, `python`, `rg`, `docker`, `curl`.
   - A CLI binary is NEVER a skill. A SKILL.md-based capability is NEVER a tool. If unsure, check: does it have a `SKILL.md`? → skill. Is it invoked via shell/MCP? → tool.

### Stage 3: Write `workflow.md` (mermaid + steps + gates)

Author `workflow.md` using [templates/workflow.md.template](templates/workflow.md.template) as the starting skeleton. Three mandatory sections:

1. `## Overview` — **mermaid `graph LR/TD`** showing Leader entry, parallel/sequential teammate nodes, decision diamonds (degraded mode branches), integration node, output node. Mermaid is the primary expression difference vs single-agent skills. **Debate pattern**: MUST keep the **inter-member communication preference** note from the template — do NOT delete it when replacing the mermaid placeholder.
2. `## Detailed Steps` — numbered steps; each step contains: executor / input / output / serial-or-parallel / **quality gate** (what to do if this step's output fails). The last step contains the final report format template.
3. `## Acceptance Criteria` — judgement of a successful single run.

Quality gates are the contract surface between stages — design them per [reference/role-design.md](reference/role-design.md) § Gate Design.

### Stage 4: Write `bind.md` (numbers + behavior + failure)

Author `bind.md` using [templates/bind.md.template](templates/bind.md.template) as the starting skeleton. Three mandatory sections:

1. `## Resource Constraints` — table with at least: `max_parallel_teammates`, `total_wall_clock_budget`, `total_token_budget`. Add per-role limits if asymmetric.
2. `## Behavioral Constraints` — **team-level** rules (Leader does not write content / teammates cannot see each other's output / Leader does not resolve contradictions / etc.). Distinct from per-role `Boundary`. **Debate pattern MUST include**: phase-scoped visibility rules (which rounds are isolated, which are mutually visible) AND the inter-member communication preference order (direct peer-to-peer > shared blackboard > Leader-relay).
3. `## Failure Handling` — must cover **(a)** teammate failure (timeout, malformed output, retry policy, how missing outputs appear in the report) and **(b)** input-overscale degradation (e.g., diff > 2000 LOC → degrade to single-role mode).

Heuristic: if a constraint has a number or an exception path, it belongs in `bind.md`. If it's the main flow, it belongs in `workflow.md`.

### Stage 5: Write `dependencies.yaml` + `SKILL.md`

The remaining two files. Write them last because they reference content produced in Stages 2–4.

**5a. `dependencies.yaml`** — use [templates/dependencies.yaml.template](templates/dependencies.yaml.template). This file codifies the **auto-matching results from Stage 2**, not a blank-slate design. Both `skills:` and `tools:` segments are mandatory — write `[]` if empty, but only after the auto-matching scan confirms no matches (signals "checked, confirmed none" — different from "never looked"). Each `roles[].skills` and `roles[].tools` declared in SKILL.md frontmatter MUST appear here (the validator enforces this).

**5b. `SKILL.md`** — use [templates/SKILL.md.template](templates/SKILL.md.template). This is the entry point that ties together all other files. Body MUST contain `## Workflow` + `## Roles` + `## Files`.

**Naming rules** (enforced by the validator):
- Directory name = `name` field in SKILL.md frontmatter (kebab-case, ends with `-swarm` by convention).
- Each `roles/<id>.md` filename MUST equal the corresponding `roles[].id` in SKILL.md frontmatter.
- All 5 files are mandatory (the validator fails on any missing file).

**Description discipline** (enforced by the validator — these are the most-violated rules):
- ≤ 4 lines, ≤ 500 chars total.
- 3-line structure only: WHAT / WHEN / NOT. Do NOT add a separate `Triggers:` line.
- NO synonym enumeration — modern semantic match handles all variants from one natural sentence.
- NO numeric scope thresholds (e.g. `">200 LOC"`) → those belong in `bind.md`.
- NO Stage 0 justification rationale → that belongs in the body's intro paragraph.
- Per-role `purpose:` field has a ≤150 char HARD CAP. Detail belongs in `roles/<id>.md`, not in SKILL.md frontmatter.

**Calibration benchmark**: aim for **≤ 500 chars** (platform hard cap: 1024). Swarm Skills are inherently more complex than single-agent skills — multi-role composition, workflow scope, and trigger scenarios may need more words. Stay concise, but don't sacrifice clarity for arbitrary brevity.

### Stage 6: Validate

Run the automated validator:

```bash
python scripts/validate_swarmskill.py path/to/<swarmskill-name>/
```

The validator checks:
- **Structural**: 5 files present, role file names match `roles[].id`, no orphan role files
- **Frontmatter**: `name` / `description` / `version` / `kind: swarm-skill` / `roles[]` present; each role has `id` + `purpose`; `name` == directory name
- **Section presence**: SKILL.md body has `## Workflow` / `## Roles` / `## Files`; each `roles/*.md` has all 5 mandatory sections; `workflow.md` has `## Overview` / `## Detailed Steps` / `## Acceptance Criteria` (and at least one mermaid block); `bind.md` has all 3 mandatory sections
- **Cross-file consistency**: every `roles[].skills` and `roles[].tools` in SKILL.md appears in `dependencies.yaml`; every `## Identity` in roles starts with a `> *"..."*` motto line
- **Output discipline**: `## Inline Persona for Teammate` present in each role file; `dependencies.yaml` skills/tools segments present even if empty

**Exit code 0 = compliant**. Non-zero exit prints the failing checks with file:line references.

The manual checklist (for design-time judgment calls the script cannot automate, like "is this content really redundant?") lives in [reference/compliance-checklist.md](reference/compliance-checklist.md). Read it before declaring the Swarm Skill done.

### Post-generation: Creation Summary + Community Enrichment

After Stage 6 passes, present a **creation summary** to the user. This is the natural point to assess capability coverage and offer community enrichment — the Swarm Skill is already complete and functional, so community search is a zero-risk enhancement.

**Summary format:**

```
Swarm Skill: <name>
Pattern: <A | B | C | Mixed>
Roles: <count> (<list of role ids>)
Validator: PASS
```

**Per-role capability coverage:**

| Role | Assigned skills/tools | Recommend community search? |
|---|---|---|
| `<role-id>` | `<matched skills/tools, or "none">` | `<Yes / No — with reason>` |

**Recommendation logic — default is to recommend:**

For each role, evaluate whether to recommend community skill search:

- **Recommend** (default) — any role that could benefit from a **domain-specific** community skill. This includes roles that already have generic utility skills (like `web-research` or `curl`) but lack **domain-specialized** skills. A generic skill is NOT a substitute for domain expertise — see the anti-pattern below.
- **Do NOT recommend** — only when the role is **inherently self-sufficient**: its task is pure reasoning, judgment, or text generation that would not benefit from any external skill or tool. Examples: an adversarial critic whose job is to find logical flaws, a copy-editor whose job is to refine prose. These roles operate entirely within the LLM's native capabilities.

**Design intent**: the agent is structurally biased toward skipping community search. Two common rationalizations:

1. *"The role has no skills → but it can function on LLM knowledge alone"* — wrong in most cases. A role *can* function, but a matching skill often dramatically improves output quality.
2. *"The role already has a generic skill (e.g. `web-research`) → covered"* — **equally wrong and harder to catch**. A generic information-retrieval skill does NOT substitute for a domain-specific operational skill. `web-research` can search the web; a Taobao shopping skill knows store tiers, coupon stacking rules, platform-specific risk signals, and API-level price tracking — capabilities that no amount of generic search can replicate.

The default must be **recommend**, with the burden of proof on **not** recommending. The test is: *"Does a domain-specific community skill exist that would give this role capabilities beyond what its current generic skills provide?"* If plausible → recommend.

> **Anti-pattern: "通用即覆盖" (generic = covered)**
>
> In `ecommerce-compare-debate-swarm`, three platform shopper roles (`taobao-shopper`, `pinduoduo-shopper`, `jingdong-shopper`) each had `web-research` assigned. The agent concluded "already covered — no community search needed." This is wrong: `web-research` is general-purpose retrieval; it knows nothing about Taobao store tiers, PDD subsidy eligibility, or JD self-operated logistics. Community registries carry platform-specific shopping and buyer-agent skills that fill exactly this gap.
>
> **Rule**: if a role's identity is tied to a specific platform or professional domain, a generic utility skill does NOT count as coverage — always recommend community search for domain-specific skills.

**High-signal scenarios — MUST recommend** (non-exhaustive):

- **Binary/office file output** (PPTX, DOCX, XLSX, PDF) — LLM cannot generate binary formats natively; needs a skill wrapping python-pptx / docx etc.
- **Diagram/chart rendering** (Mermaid, Excalidraw, matplotlib) — LLM can describe but cannot render visuals without a generation skill.
- **Specific file format parsing** (CSV, images, audio, JSON-LD) — specialized parsing/validation skill is far more reliable than raw LLM attempts.
- **Domain-specific platform / service interaction** (e-commerce platforms, social media, financial services, travel booking, healthcare systems, government portals, etc.) — community registries often have skills that encode platform-specific rules, workflows, API integrations, or operational knowledge that generic retrieval skills cannot replicate. Examples: 淘宝/天猫购物 skills with store-tier logic, 京东 price-tracking skills, 拼多多 deal-finding skills, 买手/personal-shopper skills. A role whose identity is tied to a specific platform or professional domain should **always** trigger a community search, even if generic skills like `web-research` are already assigned.

If any role hits these patterns and no **domain-specific** local skill covers it, recommend "Yes" — do not rationalize it away with "the role can still produce text instructions" or "generic retrieval already handles it."

**Community enrichment prompt:**

If one or more roles are marked "Recommend: Yes", ask:

> *"The following roles have no domain-specific skills and could benefit from community skill enhancement:*
> *[table: Role | Current skills (if any) | What domain-specific skill would help]*
> *Shall I search community skill registries? The Swarm Skill is already complete — this is an optional enhancement. (Y/N)"*

If user says **Yes**:

Read [reference/community-search.md](reference/community-search.md) and follow its full procedure. Quick summary:

1. **Derive search queries** from each role's `purpose`, `Success Criteria`, and `Output Schema` — generate 2–3 keyword variants per role (see §1 Keyword Derivation).
2. **Multi-source parallel search** — run both Tier 1 CLIs simultaneously:
   - `npx skills find '<query>'` (skills.sh — keyword matching, install counts)
   - `skillnet search '<query>' --mode vector` (SkillNet — semantic vector search, quality evaluation)
   - If results are insufficient, escalate to Tier 2 web platforms (LLMBase, SkillsMP, LobeHub) and Tier 3 curated lists (awesome-agent-skills, awesome-claude-skills).
3. **Quality gates** — every candidate must pass: install count (≥ 1K preferred), source reputation, security rating, freshness (see §3).
4. **Role-skill fit test** — verify capability match and run the removal test: "would removing this skill significantly degrade the role's output?" (see §4).
5. **Present enrichment summary** to the user with the standardized format (see §5) — full transparency on what was searched, found, selected, and rejected.
6. Install approved skills, update `roles[].skills` in SKILL.md frontmatter + `dependencies.yaml` with `source: <community-url>`.
7. Re-run `python scripts/validate_swarmskill.py` to confirm consistency.

If user says **No** → done. The Swarm Skill is fully functional as-is.

**Decision rules:**
- **Default: recommend** — empty `skills: []` = recommend community search; **also recommend** when a role only has generic utility skills (e.g. `web-research`) but lacks domain-specific skills matching its identity. Burden of proof is on NOT recommending.
- Prefer local over community — never search for capabilities already covered by a **domain-equivalent** local skill. A generic retrieval skill does NOT count as domain coverage.
- Per-role soft limit ≤ 1 community skill — if a role needs 2+, it may be too broad (consider splitting).
- Team-level soft limit: community deps ≤ role count — exceeding this signals over-engineering.
- Never force a dependency — mark `required: false` if the role can function without it.

## Roles

This skill has no Swarm Skill roles itself — it is a **single-agent skill** that authors Swarm Skills. The agent loading this skill plays the author/architect role.

## Files

| File | What it contains | When to read |
|---|---|---|
| [reference/pattern-selection.md](reference/pattern-selection.md) | A/B/C/Mixed pattern decision tree with worked examples | Stage 1a (before committing to a pattern) |
| [reference/role-design.md](reference/role-design.md) | How to author the 5 mandatory role sections; gate design between stages; anti-convergence techniques | Stage 2 + Stage 3 |
| [reference/conversion-guide.md](reference/conversion-guide.md) | Step-by-step methodology for converting a single-agent skill into a Swarm Skill, with a worked example | Stage 1b (CONVERT mode only) |
| [reference/compliance-checklist.md](reference/compliance-checklist.md) | Responsibility-attribution tests + manual-review checklist; explains what the validator can and cannot catch | Stage 6 (after the script passes) |
| [reference/community-search.md](reference/community-search.md) | Multi-source search strategy (keyword derivation, quality gates, role-skill fit test) for finding community skills | Post-generation (community enrichment step) |
| [templates/](templates/) | 5 file skeletons with placeholders and inline guidance comments | Stages 2–5 (each stage references its template) |
| [scripts/validate_swarmskill.py](scripts/validate_swarmskill.py) | Automated structural + frontmatter + section + cross-file consistency checks | Stage 6 (run on every Swarm Skill before declaring done) |

## Common pitfalls

Detailed pitfall analysis is in the reference files — read them during the corresponding stage:

- **Role authoring mistakes** (motto, boundary, persona): [reference/role-design.md](reference/role-design.md) § Putting it together: a role file checklist
- **Content quality judgment calls** (anti-convergence, gate strength, bind numbers): [reference/compliance-checklist.md](reference/compliance-checklist.md) Part B
- **Pattern selection errors** (council of clones, pipeline without gates, decomposition without disjointness): [reference/pattern-selection.md](reference/pattern-selection.md) § Anti-patterns

The three most common **structural** mistakes (caught by the validator, but worth internalizing):

1. **Skipping auto-matching in Stage 2** — jumping from role design to empty `skills: []` / `tools: []` without scanning locally available skills/tools. Fix: always run the auto-matching sub-step; empty lists are valid only after the scan confirms no matches.
2. **`roles[].skills` declared in SKILL.md but missing from `dependencies.yaml`** — silent contract violation. Fix: run the validator.
3. **Mermaid in SKILL.md body** — duplicating the diagram from `workflow.md`. Mermaid belongs only in `workflow.md`.

## Quick start

For a CREATE request:

1. Confirm Stage 0 justification with the user (which failure mode does single-agent hit?)
2. Read [reference/pattern-selection.md](reference/pattern-selection.md) and pick A / B / C / mixed
3. Write all `roles/<id>.md` files (motto, boundary, schema, inline persona) + auto-match local skills/tools
4. Write `workflow.md` → `bind.md` → `dependencies.yaml` → `SKILL.md` (each using its template)
5. Run `python scripts/validate_swarmskill.py <swarmskill-name>/` until exit 0
6. Present creation summary with per-role capability coverage → recommend community search for roles lacking **domain-specific** skills (default: recommend; generic utility skills do NOT satisfy the threshold)
7. Manual review with [reference/compliance-checklist.md](reference/compliance-checklist.md)

For a CONVERT request:

1. Read the source single-agent SKILL.md
2. Apply [reference/conversion-guide.md](reference/conversion-guide.md) decomposition checklist
3. Articulate "what is lost in single-agent form" — this is the Stage 0 justification
4. Continue from CREATE step 2 (pattern selection) onward
5. Add a `MIGRATION.md` (optional) to the new Swarm Skill explaining the source skill, the decomposition rationale, and the team-vs-single delta

For a MODIFY request:

1. Identify the change type from the impact matrix below
2. Re-run the indicated stages; apply edits honoring the section + structure rules (mottos, Forbidden+Mandatory, Inline Persona, gates, numeric bind constraints)
3. Run `python scripts/validate_swarmskill.py <swarmskill-name>/` — **Stage 6 is mandatory regardless of edit size**

#### MODIFY impact matrix

| Change type | Re-run stages | Files to update |
|---|---|---|
| Edit role Identity / Boundary / Schema | Stage 2 (affected role only) | `roles/<id>.md` |
| Add or remove a role | Stage 1a + 2 + 3 + 4 + 5 | All 5 files |
| Change workflow steps / topology | Stage 3 | `workflow.md` + `bind.md` (if constraints change) |
| Change bind constraints | Stage 4 | `bind.md` |
| Change dependencies | Stage 2 (auto-match) + 5 | `dependencies.yaml` + `SKILL.md` frontmatter |
| Edit SKILL.md description / purpose | Stage 5b | `SKILL.md` |

All rows require **Stage 6 (validator)** as the final step.
