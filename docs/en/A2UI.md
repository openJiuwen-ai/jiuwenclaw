# A2UI Generated UI

A2UI is an optional generated UI capability in JiuwenSwarm, natively supported only by the Web channel. It lets an agent return standardized UI messages for forms, confirmations, structured details, comparisons, and similar workflows, then lets the Web frontend render those messages as interactive components.

## Module Location

Backend A2UI code lives under `jiuwenswarm/server/runtime/a2ui/`. The capability belongs to the AgentServer runtime response path and is not a top-level shared package under `jiuwenswarm/`.

Frontend rendering code lives under `jiuwenswarm/channels/web/frontend/src/features/a2ui/`. The Web frontend only enters A2UI code when rendering assistant messages or sending interaction events.

## Relationships

| Module | Integration point | Responsibility |
| --- | --- | --- |
| `server/runtime/a2ui` | Backend A2UI implementation | Config parsing, Web channel policy, protocol prompts, response parsing, schema validation, repair/finalization |
| `agents/harness/common/rails` | Response prompt rail | Inject the A2UI runtime prompt only for Web channel requests when enabled; remove it for non-Web channels or when disabled |
| `server/runtime/agent_adapter` | Agent input/output adapter | Convert A2UI client events into model-readable prompts and finalize complete assistant responses only for the Web channel |
| `gateway/message_handler` | Message output path | Keep a compatibility hook; non-Web channels do not run A2UI fallback or renderer logic |
| `gateway/channel_manager/web` | Web config API | Expose the A2UI top-level toggle without exposing protocol internals |
| `channels/web/frontend/src/features/a2ui` | Web A2UI feature | Parse `<a2ui-json>` blocks, register renderers, render components, wrap action events, fill visible choice defaults |
| `channels/web/frontend/src/hooks/useWebSocket.ts` | Generic transport hook | Provide `sendStructuredChatContent` without importing A2UI feature code |

## End-To-End Flow

1. A Web user sends a natural-language message.
2. The Agent prompt rail injects the A2UI runtime prompt when the request is from the Web channel and A2UI is enabled.
3. The model may return normal text plus a `<a2ui-json>...</a2ui-json>` block for suitable UI workflows.
4. The AgentServer finalizer validates the complete assistant response and invokes the repair prompt when needed.
5. The Web frontend parses the A2UI block from the assistant message and renders components.
6. When the user clicks a button or submits a form, the frontend wraps the A2UI client event as structured chat content.
7. The backend converts the event into a model-readable prompt for the Web channel and continues the conversation.

Non-Web channels do not enter this A2UI path: no A2UI prompt injection, no A2UI client-event prompt conversion, no A2UI response finalization, and no A2UI text fallback. They continue to use the normal text/Markdown conversation path.

## Configuration

Default configuration lives in `jiuwenswarm/resources/config.yaml`:

```yaml
a2ui:
  generation_enabled: false
  rendering_enabled: false
  protocol_version: "0.8"
  stream_validation_enabled: true
  non_web_fallback_enabled: false
```

A2UI exposes two independent controls:

- `generation_enabled` controls runtime A2UI generation instructions,
  client-event handling, response finalization, repair, and retry processing.
  When disabled, Web requests receive a narrow guard: explicit A2UI generation
  requests are redirected to a plain-text alternative and told how to re-enable
  the switch, without searching for or invoking A2UI skills for generation.
  Knowledge, documentation, and code questions about A2UI remain allowed.
- `rendering_enabled` controls whether the Web frontend parses and renders A2UI
  content. When generation is disabled but rendering remains enabled, historical
  A2UI content is rendered read-only.

The Web config panel exposes both controls. They can also be overridden with
`JIUWENSWARM_A2UI_GENERATION_ENABLED` and `JIUWENSWARM_A2UI_RENDERING_ENABLED`.
Both capabilities are disabled by default.

For compatibility, legacy `a2ui.enabled` initializes both controls when their
new YAML keys are absent. `JIUWENSWARM_A2UI_ENABLED` also controls either
capability whose dedicated environment variable is absent.

`non_web_fallback_enabled` is kept only for compatibility with older configs. A2UI is currently Web-only, so non-Web channels always bypass A2UI.

Legacy `JIUWENCLAW_A2UI_*` environment variables are not supported after this PR update on June 4, 2026. Use the `JIUWENSWARM_A2UI_*` prefix for all A2UI runtime overrides.

## Dependency Versioning

The backend SDK dependency is pinned as `a2ui-agent-sdk==0.2.1` for repeatable builds. Upgrades should update the dependency lock, rerun protocol validation tests, and verify the Web renderer build in the same change.

## Boundary Rules

- A2UI is optional. Disabling it, or failing to read its config, must not break the normal text/Markdown path.
- A2UI channel support is centralized in `jiuwenswarm.server.runtime.a2ui.integration.is_a2ui_channel`; currently only `web` returns true.
- Backend host code should call only the thin `jiuwenswarm.server.runtime.a2ui.integration` facade, not protocol, repair, or schema internals.
- The WebSocket hook stays generic and must not import A2UI types or renderers.
- Web A2UI logic stays under `channels/web/frontend/src/features/a2ui/` instead of spreading into generic components or transport code.
- Non-Web channels do not perceive A2UI and do not need renderer, schema, client-event, or fallback knowledge.

## Test Coverage

Related tests live in:

- `tests/unit_tests/a2ui/`
- `tests/system_tests/test_a2ui_system_flow.py`
- `jiuwenswarm/channels/web/frontend/scripts/test-a2ui-action-defaults.mjs`

Common verification commands:

```powershell
uv run pytest tests/unit_tests/a2ui tests/system_tests/test_a2ui_system_flow.py -q
cd jiuwenswarm\channels\web\frontend
npm run build
node scripts/test-a2ui-action-defaults.mjs
```
