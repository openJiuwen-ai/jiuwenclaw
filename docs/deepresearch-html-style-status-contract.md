# DeepResearch HTML style status contract

- Change or incident: the isolated SDK style child can omit request-scoped Huawei MaaS `Authorization`, fall back to the semantic base layout, and still leave Jiuwen's report delivery successful.
- Producer repository, component, and owner: DeepSearch `report_style.service.stylize_report`; DeepSearch report export owner.
- Consumer repository, component, and owner: JiuwenSwarm `deepresearch/runtime.py`, `tools.py`, `execution.py`, and `rewrite_tools.py`; DeepResearch integration owner.
- Transport or API: isolated `sdk_bridge.py stylize-report` JSON stdout, followed by the internal stream outcome and `chat.file` metadata.
- Request and response schema: bridge request schema v2 adds `llm_auth`, which is either `{}` or exactly `{"default_headers":"{\"Authorization\":\"...\"}"}`. The response remains schema v1 with `style_applied: bool`, `style_status: applied|fallback`, and optional paired `style_phase` / `style_reason_code`. Jiuwen mirrors the diagnostics as `html_style_phase` / `html_style_reason_code` and `metadata.htmlStylePhase` / `metadata.htmlStyleReasonCode` only on diagnosed fallback.
- Version or capability negotiation: the parent and child bridge are shipped together and require request schema v2. The response remains schema v1 because the diagnostic pair is additive. A DeepSearch version without the diagnostic fields is accepted and produces `null` diagnostics; unknown or half-populated values are not propagated.
- Compatibility window: the parent and child bridge must move together for request v2. Downstream response/outcome consumers may ignore the optional diagnostic fields; `status=completed`, `report_delivered=true`, and HTML/Markdown delivery are unchanged.
- Failure and retry semantics: styling fallback still delivers the usable HTML and Markdown without retry; the completion text warns that HTML uses the base layout.
- Authentication and tenant boundary: for the `huawei-maas-session` placeholder only, Jiuwen extracts the request-scoped `Authorization` value, sends that single header in the bridge's bounded stdin JSON, binds it as a task overlay around the SDK style call, and always resets the overlay. Jiuwen does not put the value in argv, environment, stdout, or its bridge logs. Ordinary API keys send an empty auth object. No SDK source change is required.
- Observability identifiers: request ID, session ID, DeepSearch conversation ID, `style_phase`, and `style_reason_code` are logged with the fallback event. Raw exceptions, model responses, CSS, and configuration are excluded from the transport.
- Focused producer verification: the SDK is unchanged and its own suite was not rerun for this local change.
- Focused consumer verification: request-v2 allowlists, response-v1 compatibility, auth binding/reset, runtime preservation, initial and rewrite stream metadata/outcome, and completion-state regression tests.
- Cross-boundary runtime verification: pending; no service restart or live LLM request is part of this local change.
- Rollback or disable path: revert request schema v2 and the Jiuwen auth-overlay binding together while retaining the existing report delivery fallback. The SDK package remains untouched.
- Unverified assumptions: the active service has not loaded this checkout, and a new authenticated live task has not yet exercised request schema v2.
- Delivery and runtime state are reported separately from this contract; a restart and a new task are still required before claiming runtime verification.

## Layer evidence

| Layer | Checkout or version | Process or artifact | Evidence | Result |
|---|---|---|---|---|
| RelayClaw / OfficeClaw | not changed | delivered `chat.file` | consumes additive metadata | compatibility retained |
| JiuwenSwarm / JiuwenClaw | `30b7e3cd9` baseline plus local change | isolated SDK bridge and DeepResearch tool | focused auth/style tests plus full DeepResearch suite with pytest unraisable cleanup disabled | 961 passed, 1 dependency warning |
| DeepSearch SDK | `28f28deae` baseline, 0.2.0 | `ReportExportResult`; bridge response schema v1 | SDK is untouched; Jiuwen owns request schema v2 and auth binding | SDK suite not rerun |
