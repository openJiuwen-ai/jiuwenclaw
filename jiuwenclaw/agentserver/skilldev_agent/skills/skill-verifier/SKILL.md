---
name: skill-verifier
description: Run the verification gate on a skill under <workspace>/skill/. Returns PASS or FAIL with details. Provides three entry points — validate (local spec check only), safety_scan (remote risk scan for a given URL), and gate (full pipeline with short-circuit). Use whenever a skill needs spec validation, packaging, uploading, or risk scanning before it can be listed. Never creates or edits skill content — only checks and packages.
---

# Skill Verifier

Pure verification gate for skills. Does not create, edit, or interact — only checks, packages, uploads, and scans.

## Three entry points

### 1. `validate` — local spec check (cheap, fast)

```bash
cd "<skill-verifier-dir>" && python3 -m scripts.validate <workspace>
```

Checks SKILL.md frontmatter (name, description, allowed keys), body length, and static security. Returns `Validation passed.` or `Validation failed:` with details. Use this for rapid feedback during fix loops and as a guardrail during evaluation/description-optimization iterations.

### 2. `safety_scan` — remote risk scan for a known URL

```bash
cd "<skill-verifier-dir>" && python3 -m scripts.safety_scan <skill-name> <url>
```

Submits a URL to the platform risk-scanning service and polls for a result. Returns `Safety scan passed.` or `Safety scan failed:` with details. Use this when the skill package URL is already available (e.g. from an uploaded import package), saving one upload round-trip.

### 3. `gate` — full pipeline with short-circuit

```bash
cd "<skill-verifier-dir>" && python3 -m scripts.gate <workspace>
```

Orchestrates the complete verification pipeline:

```
validate (local)
  └─ FAIL → return immediately, no packaging / uploading / scanning
  └─ PASS ↓
package (zip with dependency references, excludes *.bak-*)
  └─ FAIL → return immediately
  └─ PASS ↓
upload (to OBS, returns CDN URL)
  └─ FAIL → return immediately
  └─ PASS ↓
safety_scan (remote, uses uploaded URL)
  └─ return PASS or FAIL with details
```

On success, prints the packaged file path and uploaded URL. On any failure, prints the stage and failure details. The short-circuit ensures that spec violations never trigger the expensive upload + remote scan.

## Spec rules (single source of truth)

The full specification for SKILL.md structure, frontmatter constraints, and description limits is in `references/skill_spec.md`. The validation scripts use this file as the canonical rule set.

## Hard rules

1. Never create or modify skill content — this is a read-only gate.
2. Never interact with the user — return structured PASS/FAIL output only.
3. Never skip `validate` before `package` in the gate pipeline.
4. Always short-circuit on failure — do not proceed to later stages.
