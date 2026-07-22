# DeepResearch whole-selection rewrite highlighting

## Goal

For every rewrite action, when at least one selected editable slot changes, highlight all visible editable content corresponding to the original selection in the new document.

## Semantics

- Apply the same behavior to every rewrite action, including `expand` and `rewrite`.
- Treat the operation as producing a rewrite result when at least one selected editable slot differs from its original visible text.
- Once triggered, generate highlight ranges for every visible editable span inside the original selection as mapped into the child document, including unchanged bold numbers and unchanged bold phrases.
- Keep partial selections bounded to their original start and end; do not expand a highlight to the rest of the Markdown unit.
- For selections spanning multiple units, highlight all selected visible spans when any selected editable slot changes.
- A no-op rewrite produces no highlight ranges. Fully deleted content has no visible range to highlight.

## Protected content

Highlight ranges continue to be produced from editable slots through the Markdown rewrite map. Therefore they exclude:

- Markdown syntax bytes such as `**` and escape markers;
- protected citation labels and citation destinations;
- other protected anchors already excluded from editable slots.

## Implementation boundary

Change only JiuwenClaw's rewrite-highlight range generation. Keep the provenance schema, UTF-8 byte offsets, OfficeClaw renderer, range-count limit, and fail-closed behavior unchanged.

## Verification

Add backend tests covering:

- a changed plain-text slot with an unchanged bold number in the same selection;
- an unchanged bold phrase in a selection containing another changed slot;
- protected citation label and destination inside the highlighted selection;
- partial and multi-unit selections;
- no-op and fully deleted results;
- existing range-count bounds.

Retain the OfficeClaw renderer regression proving that byte ranges highlight visible bold/link text while excluding Markdown syntax and protected citations.
