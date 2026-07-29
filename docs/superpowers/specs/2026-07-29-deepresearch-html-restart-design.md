# DeepResearch HTML Follow-up Restart Design

## Goal

When a DeepResearch rewrite has completed successfully, an explicit follow-up such
as `生成 HTML` must generate HTML for that exact committed rewrite even after the
JiuwenClaw process restarts.

## Scope

The change is limited to JiuwenClaw's DeepResearch rewrite adapter path.
OfficeClaw API/Web code, runtime configuration, the HTML generation tool, and
OpenJiuwen's global checkpointer behavior remain unchanged.

## Design

The rewrite fast path already receives a trusted `deepresearch_commit_rewrite`
result and flushes the turn through the adapter's tenant-scoped persistent
checkpointer. Extend that checkpoint state with one versioned
`pending_html_export` record containing only:

- `report_path`
- `revision_id`

Before a plain request enters the Agent/LLM loop, detect the documented,
unambiguous HTML follow-up phrases. For such a request, restore the same
session directly through `self._checkpointer`, read and validate
`pending_html_export`, and invoke `deepresearch_generate_rewrite_html` exactly
once. The request must not enter the ordinary Runner, whether the target exists
or not.

This is deliberately independent of assistant text, user-supplied paths,
filesystem modification time, and model interpretation. The existing HTML tool
remains the authority for workspace, provenance, revision, hash, generation,
and delivery validation.

## Data and Isolation

The checkpoint key remains scoped by the existing tenant workspace, Agent card,
and `session_id`. A target written in one session cannot be read from another
session. A failed rewrite does not replace the last successful target.

The record has an explicit schema version and exact keys. Invalid, missing, or
partially restored records fail closed. The record has no time-based expiry,
matching the requirement that elapsed time alone must not break the follow-up.

## Request Handling

Supported deterministic phrases are a small allowlist after trimming whitespace
and terminal punctuation:

- `生成 HTML`
- `请生成 HTML`
- `生成最终美化版 HTML`
- `请生成最终美化版 HTML`

Letter case and whitespace inside `HTML` are normalized. Rewrite envelopes do
not match this handler.

Outcomes:

- Valid target and successful tool result: `已生成美化后的 HTML。`
- Valid target and failed tool result: `HTML 生成失败，但 Markdown 改写版本仍然成功保留。`
- Missing or invalid target: `未找到可生成 HTML 的已完成改写版本。`

Failures do not fall through to the LLM, retry the tool, rewrite the report, or
guess a target.

## Testing

Unit tests cover intent recognition, trusted-target validation, persistence,
session isolation, success/error responses, single tool invocation, and Runner
bypass.

A restart regression uses two independently created persistent checkpointer
instances pointing at the same temporary SQLite database: the first persists a
successful rewrite target, and the second restores it for the same session and
executes the HTML follow-up. This proves disk restoration rather than
same-process in-memory reuse.
