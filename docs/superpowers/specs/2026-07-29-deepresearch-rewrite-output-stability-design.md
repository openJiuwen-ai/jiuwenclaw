# DeepResearch Rewrite Output Stability Design

## Context

The DeepResearch rewrite fast path performs `prepare -> model -> commit` for
`polish`, `expand`, and `shorten`. The prepare and commit boundaries are strict:
the model may change only slot text, while the server preserves Markdown
topology, citations, provenance, and immutable revision lineage.

On 2026-07-29, a valid v5 polish request completed preparation but returned
`MODEL_OUTPUT_INVALID` before commit. The model response contained a complete
JSON object wrapped in a `json` code fence and copied the input-only `format`
field into every output slot. The four immediately preceding requests in the
same session returned bare JSON without the extra field and succeeded. The
failed request did not create v6 or mutate v5.

## Goals

- Recover deterministic framing mistakes that cannot affect report semantics:
  one complete JSON code fence and known input-metadata echoes.
- Reduce the probability of metadata echoes by ensuring model-visible slots
  contain only fields the model is allowed to return.
- Retry one model generation after an invalid pre-commit result.
- Preserve strict identity, order, text type, `facts_added=false`, forbidden
  syntax, size, topology, citation, provenance, and revision validation.
- Preserve accurate model-call count, total model latency, and token/cost usage
  when a retry occurs.
- Keep the change local to JiuwenClaw. Do not modify OfficeClaw, runtime
  configuration, persistent data, or the active service process.

## Non-goals

- Do not accept trailing prose, multiple JSON objects, unknown fields, missing
  fields, changed unit or slot identifiers, reordered units or slots,
  `facts_added=true`, forbidden Markdown syntax, or oversized output.
- Do not retry commit or write a child revision more than once.
- Do not weaken the existing commit validators.
- Do not depend solely on provider-specific structured-output support.
- Do not change rewrite semantics, temperature, length guidance, or provenance
  format.

## Considered Approaches

### Prompt-only enforcement

Strengthening “return JSON only” is low cost but does not remove the
probabilistic failure mode. The current system prompt already states the exact
shape and forbids Markdown fences.

### Provider-enforced JSON Schema only

Strict `response_format` or JSON Schema is preferable when the configured
OpenAI-compatible gateway supports it. The active GLM-5.2 gateway capability
has not been verified, so enabling it unconditionally could turn successful
rewrites into transport failures. This remains a follow-up compatibility
probe, not the sole protection.

### Layered local boundary hardening

Project model input to the output schema, safely canonicalize known framing
mistakes, retain strict validation, and retry one invalid pre-commit generation.
This approach is provider-independent and directly covers the observed
failure. It is the selected design.

## Design

### Model-input projection

Before building the user message, transform prepared units into:

```json
[
  {
    "unit_id": "unit_1",
    "slots": [
      {"slot_id": "slot_1", "text": "selected visible text"}
    ]
  }
]
```

The model must not receive `format`, `link_id`, `type`, `level`,
`list_depth`, or `list_marker` inside the output-shaped `units` field.
Readonly cohesion context and citation evidence remain unchanged. The
prepare context token retains all topology metadata for commit.

Malformed internal prepared units stop before the model with the existing safe
internal-error behavior.

### Model-output decoding and canonicalization

The decoder receives the model content plus the projected expected units.

1. Require a non-empty string.
2. Parse bare JSON first.
3. If bare parsing fails, accept only one complete outer
   ```` ```json ... ``` ```` or ```` ``` ... ``` ```` fence with optional
   surrounding whitespace. Reject any prefix or suffix outside that fence.
4. Require exactly `units` and `facts_added` at the top level, with
   `facts_added` equal to `false`.
5. Require the exact expected unit and slot count, order, and identifiers.
6. Require exact unit keys `unit_id` and `slots`.
7. For slots, require `slot_id` and `text`. Permit only the known ignored
   metadata keys `format` and `link_id`; discard them before commit. Reject
   every other extra key.
8. Return a newly constructed canonical object containing only
   `unit_id`, `slots`, `slot_id`, `text`, and `facts_added`.

Discarding `format` and `link_id` cannot change the report because commit
reconstructs protected topology from the server-owned context token. The
existing commit validator remains the final defense.

### One bounded model retry

Run at most two model calls:

- Call 1 uses the existing prompt and sampling behavior.
- If decoding or canonical validation fails before commit, call the model once
  more with the same projected payload and an additional strict-retry system
  instruction.
- If call 2 is invalid, return `MODEL_OUTPUT_INVALID`.
- If either model call raises a transport/provider exception, return
  `MODEL_CALL_FAILED`.
- Commit is invoked only after a canonical result exists and is never retried.

Total model latency covers both calls. Usage metadata sums numeric token and
cost fields across both responses. `model_calls` reports the actual number of
model invocations.

### Observability

`RewriteFastPathResult` records:

- whether the accepted output required code-fence or metadata normalization;
- the final internal decode-rejection reason when both attempts fail;
- the actual model-call count.

The adapter log emits these fields without logging rewritten report text or
the context token. User-facing errors remain stable and safe.

## Error Handling

- Preparation failures retain their existing error codes and never call the
  model.
- Invalid model output is retried once, then returns
  `MODEL_OUTPUT_INVALID: invalid structured rewrite result`.
- Provider exceptions return `MODEL_CALL_FAILED`.
- Commit validation and write failures retain current behavior.
- A failed model result never consumes the context token through commit and
  never creates a child revision.

## Test Strategy

Focused unit tests must prove:

- model-visible units exclude input-only structural metadata;
- the exact observed `json` fence plus `format` echo is canonicalized and
  committed once;
- bare valid JSON remains a one-call success;
- trailing text, unknown keys, changed IDs/order, and `facts_added=true` are
  never committed;
- an invalid first result followed by a valid second result commits once and
  reports two model calls;
- two invalid results return `MODEL_OUTPUT_INVALID` without commit;
- a retry provider exception returns `MODEL_CALL_FAILED` without commit;
- token and cost usage from both calls is accumulated;
- normalization and rejection reasons reach the adapter log;
- existing fast-path, rewrite-tool, and document-rewrite tests remain green.

## Acceptance Criteria

- The captured 2026-07-29 failure shape completes through canonicalization
  without weakening commit validation.
- No child revision is created from an invalid or partially validated result.
- All focused fast-path, adapter, rewrite-tool, and document-rewrite tests pass.
- `compileall` and `git diff --check` pass.
- A live provider soak test is a separate, explicitly authorized validation
  because it consumes model quota; it is not required for the local commit.
