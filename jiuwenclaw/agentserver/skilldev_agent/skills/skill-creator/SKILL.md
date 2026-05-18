---
name: skill-creator
description: Create new skills, modify and improve existing skills, and measure skill performance. Use when users want to create a skill from scratch, edit, or optimize an existing skill, run evals to test a skill, benchmark skill performance with variance analysis, or optimize a skill's description for better triggering accuracy.
---

# Skill Creator

A skill for creating new skills and iteratively improving them.

The flow:

1. **Talk to the user first** — understand what they want before writing anything.
2. Write a draft of the skill.
3. **Ask the user if they want evals** — get explicit consent.
4. If yes: propose test cases, run with-skill and baseline, grade via `agents/grader.md`, aggregate via `scripts.aggregate_benchmark`, present results.
5. Iterate. At the end of each round, ask the user via `ask_user_question`: *continue improving* or *move on to the next step*. Do NOT mention packaging at this point.
6. **Ask the user whether to optimize the description** — get explicit consent. Mandatory gate.
7. **Package the skill** — always execute.

Your TODO plan should mirror this workflow with one task per phase — at minimum: capture intent, draft SKILL.md, eval consent, run & grade evals, iterate, description-optimization consent, package. Do not collapse evaluation and iteration into a single "run evals or package" task; they are distinct phases with their own checkpoints.

**Hard rules — violating any of these is a bug:**
1. Don't write before talking to the user.
2. Don't skip the eval consent question (Step 3).
3. Don't skip grading/aggregation — runs without `grading.json` and `benchmark.md` are worthless.
4. Don't skip the description optimization consent question (Step 6).
5. Don't skip packaging (Step 7).

Self-check at every checkpoint: if you're about to move past one of these without the explicit confirmation or artifact, **stop and go back**.

---

## Skill anatomy

```text
skill-name/
├── SKILL.md       required — YAML frontmatter + instructions
├── scripts/       optional — deterministic or repeated operations
├── references/    optional — load-on-demand domain docs, schemas, API details
└── assets/        optional — templates, icons, fonts used in outputs
```

### Frontmatter — hard constraints

```yaml
---
name: skill-name-here
description: Imperative description of when to trigger and what to do.
---
```

- `name`: kebab-case, lowercase letters / digits / hyphens only, ≤ 30 chars.
- `description`: ≤ 1024 chars. This is the **only triggering mechanism** — all "when to use" guidance goes here, not the body. Make it slightly pushy: instead of `"Builds dashboards for internal data"`, write `"Builds dashboards for internal data. Use whenever the user mentions dashboards, metrics, or wants to display company data — even if they don't say 'dashboard' explicitly."`
- Allowed keys only: `name`, `description`, `license`, `allowed-tools`, `metadata`, `compatibility`. No duplicates.

### Progressive disclosure

- Metadata (name + description) is always in context — keep it lean and trigger-accurate.
- Body is loaded on trigger — keep it under ~300 lines.
- Large reference material (API specs, schemas, variant docs) lives in `references/` and is read on demand. For multi-domain skills, split by variant (`aws.md`, `gcp.md`, …).
- Repeated, deterministic, error-prone operations belong in `scripts/`.

### Writing principles

- Imperative form. No "this skill will…".
- Give the model a mental model and judgment criteria, not a script.
- Include examples where they clarify behavior.

---

## Step 1: Capture intent

Before writing anything, extract what you can from the conversation — tools used, steps taken, corrections made — then fill gaps:

1. What should this skill do? When should it trigger?
2. What's the expected output?
3. Edge cases, input formats, dependencies?

Surface things the user might not have considered: failure modes, what "done" looks like. Research similar skills if useful. Only move on once aligned.

---

## Step 2: Write the SKILL.md

Follow the anatomy and frontmatter rules above. Self-check before moving on:

- Create or update the skill under the current workspace's `skill/` directory: `<workspace>/skill/<skill-name>/`.
- `SKILL.md` exists with valid frontmatter (kebab-case name ≤ 30, description ≤ 1024, allowed keys only).
- Body is under 300 lines; bulky reference material moved to `references/`.
- No stray files outside the skill folder.

---

## Step 3: Ask about evals, then propose test cases

After drafting, stop and ask:

> "The skill draft is ready. Would you like me to run evaluations to test it?"

- **No** → skip to Step 6 (Step 5 is bypassed since there are no eval results to iterate on).
- **Yes** → propose 2–3 realistic test prompts, each with objectively verifiable expectations. Present prompts and expectations together:

> "Here are a few test cases and the expectations I'll grade them on. Do these look right?"

Save to `<workspace>/evals/evals.json` (schema in `references/schemas.md`). Test types worth covering: `smoke` (minimal input works), `happy_path` (real user flow), `edge_case` (boundary/error input), `integration` (multi-step end-to-end).

---

## Step 4: Run the evals

Every test case needs **both** a with-skill run and a baseline (no-skill for new skills, old-skill snapshot for improvements). Never fabricate. Put all evaluation artifacts under the current workspace's `evals/` directory, for example: `<workspace>/evals/iteration-<N>/eval-<N>/`.

**Subagent execution rules:**
- **Always pass the workspace path explicitly.** Subagents do not inherit your system prompt and therefore don't know the workspace — state it at the top of the prompt (e.g. `"Workspace: /abs/path/to/workspace. All file reads/writes must stay inside it."`) and use absolute paths for every input/output you reference. The same applies to any subagent you spawn outside the eval flow (grader, description-optimization runs).
- Include in the prompt: *"You are running non-interactively — no human will provide stdin. Feed expected inputs via heredoc/pipe. Never leave a command waiting for stdin."*
- Prefer isolated subagents for runs; only fork (inherit parent context) when the subtask genuinely needs the full conversation.

### 4a: Per test case — write metadata, run both configs, capture timing

Process each test case sequentially and completely.

**1.** Write `eval_metadata.json` to `<workspace>/evals/iteration-<N>/eval-<N>/eval_metadata.json`. Copy the expectations the user already approved — do not re-draft.
```json
{
  "eval_id": 0,
  "eval_name": "descriptive-name-here",
  "prompt": "The user's task prompt",
  "expectations": []
}
```

**2.** Run with-skill subagent. Pass skill path, task prompt, input files, output dir (`with_skill/outputs/`).

**3.** **Immediately** save `with_skill/timing.json` using the values from the subagent's task notification. This data cannot be recovered later.
```json
{ "total_tokens": 84852, "duration_ms": 23332, "total_duration_seconds": 23.3 }
```

**Never fabricate timing.** Fabricated timing poisons every downstream comparison and makes the whole eval untrustworthy.

**4.** Run baseline subagent:
- **New skill:** same prompt without the skill path → `without_skill/outputs/`.
- **Improving a skill:** snapshot the old skill first, run with the snapshot → `old_skill/outputs/`. Do NOT run a no-skill baseline — compare old vs new.

**5.** **Immediately** save the baseline `timing.json`.

If a run fails: diagnose and retry. Do not proceed with missing runs.

**Parallel execution:** you may fan out all runs in parallel (e.g. 6 subagents at once for 3 cases × 2 configs) — but each task notification carries its own `total_tokens` / `duration_ms`, and **every per-task timing must be written to its run's `timing.json` before grading or aggregation starts**. The common failure mode is collecting only one aggregate number and losing the per-run breakdown — once the notifications are gone, the data is gone. If you can't reliably attribute each notification back to its run directory, run them serially instead.

### 4b: Grade, aggregate, present

Hard checkpoint — you may not present results until every run has `grading.json` and `benchmark.md` exists.

1. **Grade each run** via grader subagent (serial). The prompt must tell the subagent to **read `agents/grader.md` first and follow it exactly**. Pass expectations from `eval_metadata.json`, transcript path, outputs dir. Output: `grading.json` per run (schema in `references/schemas.md`).
2. **Aggregate:**
   ```bash
   python3 <skill-dir>/scripts/aggregate_benchmark.py <workspace>/evals/iteration-N --skill-name <name>
   ```
   Resolve `<skill-dir>` to the current `skill-creator` directory you are using for this task.
   Produces `<workspace>/evals/iteration-N/benchmark.json` and `<workspace>/evals/iteration-N/benchmark.md`. Confirm both exist.
3. **Surface patterns** from `benchmark.md` — which expectations failed, where variance is high, what the skill cost in time/tokens.
4. **Present** per test case. Show the user everything in `benchmark.md` — **time and tokens are mandatory columns, not optional**. Pass-rate-only summaries are a bug. You must present the final `benchmark.md` to the user, not just summarize it loosely. Ask "Any feedback?" each time. Finish with the overall summary from `benchmark.md`.

Self-check before presenting: (a) every run dir has `grading.json`, (b) `benchmark.md` exists, (c) you're showing graded scores, not your own judgment, (d) time and tokens are visible for every case. If any are false, go back.

---

## Step 5: Improve and iterate

1. Read **transcripts**, not just final outputs — trim unproductive steps the skill caused.
2. Generalize — avoid narrow fixes that only pass the tested examples.
3. Explain the *why* — don't just add rules.
4. Bundle repeated work — if every run wrote the same helper, lift it into `scripts/`.
5. Apply changes and rerun into `<workspace>/evals/iteration-<N+1>/`.

**Exit question (mandatory after every round):** use `ask_user_question` with exactly two options. Do NOT mention packaging — that is Step 7's concern, not this fork's.

1. **Continue improving** — go back to substep 5.
2. **Move on to the next step** — exit Step 5 and enter Step 6.

---

## Step 6: Description optimization

**Mandatory consent gate** — runs immediately after exiting Step 5 (or after the user declines evals in Step 3). Use `ask_user_question`:

> "Would you like me to optimize the skill's description for better triggering accuracy?"

- **Yes** → **read `references/description-optimization.md` now, before anything else.** It contains the full protocol — query generation, review, agent-driven eval loop, trigger detection, scoring. Don't improvise from memory. When the protocol finishes, continue to packaging.
- **No** → continue to packaging.

---

## Step 7: Packaging

Run:

```bash
python3 <skill-dir>/scripts/package_skill.py <workspace>/skill/<skill-name> <workspace>/output
```

Resolve `<skill-dir>` to the current `skill-creator` directory you are using for this task.

Use the skill directory created earlier at `<workspace>/skill/<skill-name>/` and write the final archive to `<workspace>/output/<skill-name>.zip`.

If you have access to `present_files`, also present the packaged output from the workspace `output/` folder.

Self-check before ending the conversation: did `scripts/package_skill.py` run? If not, run it now.

---

## Reference files

- `agents/grader.md` — grading expectations against outputs
- `references/description-optimization.md` — full description optimization process
- `references/schemas.md` — JSON schemas for evals.json, grading.json, etc.