---
name: skill-creator
description: Create new skills, modify and improve existing skills, and measure skill performance. Use when users want to create a skill from scratch, edit, or optimize an existing skill, run evals to test a skill, benchmark skill performance with variance analysis, or optimize a skill's description for better triggering accuracy.
---

# Skill Creator

A skill for creating new skills and iteratively improving them.

The default flow:

1. **Talk to the user first** - understand what they want before writing anything.
2. **Write or update the skill files** - keep the skill focused, safe, and consistent with the workspace.
3. **Package the skill** - always execute once the skill files are ready.

Optional branches:

- **Evaluations are opt-in only.** If the user explicitly asks to test, evaluate, benchmark, compare, or iterate using eval results, read `references/evaluation.md` and follow it exactly.
- **Description optimization is opt-in only.** If the user explicitly asks to optimize the skill description or improve triggering accuracy, read `references/description-optimization.md` and follow it exactly.
- If an optional branch changes the skill files, package the skill again before ending.

Your TODO plan should mirror the active workflow:

- Default work: capture intent, write or update skill files, package.
- Add evaluation tasks only when the user explicitly requested evals or benchmark-style testing.
- Add description-optimization tasks only when the user explicitly requested trigger or description optimization.

**Hard rules — violating any of these is a bug:**
1. Don't write before talking to the user.
2. Don't ignore security red lines: no dangerous commands, hardcoded credentials, or path traversal in the skill body or scripts.
3. Don't skip packaging.
4. Don't write a Chinese or otherwise invalid value to the skill `name`, even if the user asks for it. Refuse that specific rename and offer a valid ASCII kebab-case alternative.

---

## Step 1: Capture intent

Before writing anything, extract what you can from the conversation — tools used, steps taken, corrections made — then fill gaps:

1. What should this skill do? When should it trigger?
2. What's the expected output?
3. Edge cases, input formats, dependencies?

Surface things the user might not have considered: failure modes, what "done" looks like. Research similar skills if useful. Only move on once aligned.

---

## Step 2: Write the skill files

###  Skill anatomy

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

- `name`: machine-readable ID, not a display title. It must match `^[a-z0-9-]+$`, use lowercase letters / digits / hyphens only, be ≤ 64 chars, not start/end with `-`, not contain `--`, and exactly match the skill directory name. If the user asks for a Chinese name, keep or choose a valid ASCII kebab-case name instead; Chinese belongs in `description` or the body, not `name`.
- `description`: This is the **only triggering mechanism** — all "when to use" guidance goes here, not the body. Chinese SHOULD be ≤ 256 chars and MUST be ≤ 512 chars; English SHOULD be ≤ 512 chars and MUST be ≤ 1024 chars. Make it slightly pushy: instead of `"Builds dashboards for internal data"`, write `"Builds dashboards for internal data. Use whenever the user mentions dashboards, metrics, or wants to display company data — even if they don't say 'dashboard' explicitly."`
- Allowed keys only: `name`, `description`, `license`, `allowed-tools`, `metadata`, `compatibility`. No duplicates.
- External dependencies belong in `metadata`: `metadata.tools` for function tools, `metadata.agents` for agent tools, and `metadata.clis` for CLI tools. Tool entries must include `pluginId` and `toolName`.
- If the skill uses function tools, agent tools, or CLI tools, read the matching usage reference before writing instructions and include one concrete example instruction sentence in the skill body.

### Progressive disclosure

- Metadata (name + description) is always in context — keep it lean and trigger-accurate.
- Body is loaded on trigger — keep it under ~300 lines.
- Large reference material (API specs, schemas, variant docs) lives in `references/` and is read on demand. For multi-domain skills, split by variant (`aws.md`, `gcp.md`, …).
- Repeated, deterministic, error-prone operations belong in `scripts/`.
- Packaged external dependency definitions are copied into `references/` automatically by the packager.

### Writing principles

- Imperative form. No "this skill will…".
- Give the model a mental model and judgment criteria, not a script.
- Include examples where they clarify behavior.
- Body structure can reference these sections as needed: domain knowledge, tool definitions, exemplar playbook, SOP, safety red lines, and human collaboration.

### Device-side `scripts/` generation gate

A skill executes on the device when:
- `metadata.clis` is non-empty, OR
- any entry in `metadata.tools` has `pluginType: Device`.

Device-side skills must not generate `scripts/` by default. If a script is genuinely required after evaluation, call `ask_user_question` to confirm with the user that the skill includes a Python script, running it on the device will be slow, and let them choose to proceed with generation or adjust the feature. Only generate the script after explicit confirmation.

### Self-check before moving on

- Create or update the skill under the current workspace's `skill/<skill-name>` directory: `<workspace>/skill/<skill-name>/`.
- `SKILL.md` exists with valid frontmatter (name matches directory, description within language-specific limits, allowed keys only).
- If the skill declares `metadata.tools`, read `references/usage_tools.md` and add one example sentence showing the `function_call_tool` call shape.
- If the skill declares `metadata.agents`, read `references/usage_agents.md` and add one example sentence showing the `agent_as_a_tool` call shape.
- If the skill declares `metadata.clis`, read `references/usage_clis.md` and add one example sentence showing the `exec` command shape.
- If the skill declares any of `metadata.tools` / `metadata.agents` / `metadata.clis`, the body must include a single **tool definitions** section listing every registered tool.
- Body is under 500 lines; bulky reference material moved to `references/`.
- Security validation passes: no dangerous commands, hardcoded credentials, or path traversal in the skill body or scripts.
- No stray files outside the skill folder.

---

## Step 3: Packaging

Run Bash:

```bash
cd "<skill-creator-dir>" && python3 -m scripts.package_skill <workspace>/skill/<skill-name> <workspace>/output
```

If a declared dependency source file is missing, packaging fails. Fix the metadata or source JSON instead of inventing replacement files.

If you have access to `present_files`, also present the packaged output from the workspace `output/` folder.

Self-check before ending the conversation: did `scripts/package_skill.py` run? If not, run it now.

---

## Optional Workflows

- `references/evaluation.md` - full opt-in evaluation and benchmark process.
- `references/description-optimization.md` - full opt-in description optimization process.
- `references/schemas.md` - JSON schemas for evals.json, grading.json, etc.

If the user explicitly requested evals, run the evaluation workflow after drafting and before final packaging. If eval-driven iteration changes files, repeat evaluation steps as needed and package after the last change.

If the user explicitly requested description optimization, run the description-optimization workflow after the skill draft is coherent and before final packaging. Package after any description change.
