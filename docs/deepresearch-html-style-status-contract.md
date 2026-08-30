# DeepResearch HTML style status contract

- Change or incident: SDK HTML styling can fall back to the semantic base layout while Jiuwen still reports successful delivery.
- Producer repository, component, and owner: DeepSearch `report_style.service.stylize_report`; DeepSearch report export owner.
- Consumer repository, component, and owner: JiuwenSwarm `deepresearch/runtime.py`, `tools.py`, `execution.py`, and `rewrite_tools.py`; DeepResearch integration owner.
- Transport or API: isolated `sdk_bridge.py stylize-report` JSON stdout, followed by the internal stream outcome and `chat.file` metadata.
- Request and response schema: bridge schema v1 keeps `style_applied: bool` and `style_status: applied|fallback`, and adds optional paired `style_phase` / `style_reason_code`. Jiuwen mirrors them as `html_style_phase` / `html_style_reason_code` and `metadata.htmlStylePhase` / `metadata.htmlStyleReasonCode` only on diagnosed fallback.
- Version or capability negotiation: bridge schema version remains 1 because the diagnostic pair is additive. A DeepSearch version without the fields is accepted and produces `null` diagnostics; unknown or half-populated values are not propagated.
- Compatibility window: existing consumers may ignore the new fields; `status=completed`, `report_delivered=true`, HTML/Markdown files, and the fallback warning are unchanged.
- Failure and retry semantics: styling fallback still delivers the usable HTML and Markdown without retry; the completion text warns that HTML uses the base layout.
- Authentication and tenant boundary: unchanged; styling continues to use the request-scoped DeepResearch model configuration and tenant route.
- Observability identifiers: request ID, session ID, DeepSearch conversation ID, `style_phase`, and `style_reason_code` are logged with the fallback event. Raw exceptions, model responses, CSS, and configuration are excluded from the transport.
- Focused producer verification: model invocation failure, empty CSS, invalid CSS response, and successful styling cover the diagnostic result contract.
- Focused consumer verification: bridge allowlist and backward compatibility, runtime preservation, initial and rewrite stream metadata/outcome, and completion-state regression tests.
- Cross-boundary runtime verification: pending; no service restart or live LLM request is part of this local change.
- Rollback or disable path: revert the optional status propagation and warning while retaining the existing report delivery fallback.
- Unverified assumptions: the already completed 08/30 07:30 task predates these fields, so its exact model-side reason cannot be recovered from the existing artifact or logs.
- Delivery and runtime state are reported separately from this contract; a restart and a new task are still required before claiming runtime verification.

## Layer evidence

| Layer | Checkout or version | Process or artifact | Evidence | Result |
|---|---|---|---|---|
| RelayClaw / OfficeClaw | not changed | delivered `chat.file` | consumes additive metadata | compatibility retained |
| JiuwenSwarm / JiuwenClaw | `61e0899b5` baseline | isolated SDK bridge and DeepResearch tool | 259 directly touched tests; full DeepResearch suite with pytest unraisable cleanup disabled | 926 passed, 1 dependency warning |
| DeepSearch SDK | `28f28deae` baseline, 0.2.0 | `ReportExportResult`, bridge schema v1 | report styling/export plus non-router convert tests | 99 passed plus 6 passed; 2 router tests unavailable because the reused environment lacks `pymysql` |
