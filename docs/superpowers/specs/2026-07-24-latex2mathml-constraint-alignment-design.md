# LaTeX2MathML Constraint Alignment Design

## Goal

Allow JiuwenClaw's direct `latex2mathml` requirement to coexist with the
`openjiuwen-deepsearch` `enterprise_dev` requirement of
`latex2mathml==3.78.1`.

## Scope

- Change the direct dependency range in `pyproject.toml` from
  `latex2mathml>=3.81.0` to `latex2mathml>=3.78.1`.
- Change the mirrored JiuwenClaw dependency specifier in `uv.lock` to the same
  range.
- Keep the currently locked `latex2mathml` artifact at `3.81.0`.
- Do not refresh the `openjiuwen-deepsearch` Git revision.
- Do not change the current virtual environment.
- Do not add or update `mathml2omml` dependencies in this change.

## Rationale

The new range includes both the current locked version (`3.81.0`) and the
version required exactly by the current DeepSearch branch (`3.78.1`). This
removes JiuwenClaw's direct lower-bound conflict without causing unrelated Git
dependency or environment updates.

## Verification

1. Confirm `pyproject.toml` and the JiuwenClaw metadata entry in `uv.lock` both
   declare `latex2mathml>=3.78.1`.
2. Confirm `uv.lock` still locks the package artifact at `3.81.0`.
3. Run `uv lock --check` to validate that the checked-in lock remains
   consistent with the project metadata.
4. Run `git diff --check` and confirm only the two dependency files change.

## Deferred Work

Refreshing `openjiuwen-deepsearch` to its latest `enterprise_dev` revision and
resolving the complete environment to `latex2mathml==3.78.1` is a separate,
broader dependency update.
