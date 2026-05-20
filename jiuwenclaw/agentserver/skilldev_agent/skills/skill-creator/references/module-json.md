# HarmonyOS module.json

Use this reference when a skill needs more than the minimal:

```json
{
  "version": "1.0.0",
  "toolDependencies": []
}
```

## Fields

- `version` (required): Semver string. Bump major for incompatible dependency or triggering changes, minor for compatible capability additions, patch for fixes.
- `availableOn`: Agent runtime targets. Allowed values: `phone`, `tablet`, `pc`, `wearable`, `car`, `tv`, `cloud`, `cloudSandbox`, `localSandbox`.
- `toolDependencies`: Platform-registered Function or CLI tools only. Do not include built-ins (`exec`, `invoke`, `read`, `write`, `web_search`, `web_fetch`, `load_skill`) or private scripts.
- `abilityName`: Ability used by ETS scripts. Omit when there are no ETS scripts.
- `visibility`: App-level visibility. Use `private`, `system`, or `public`; default is `system`.
- `srcEntries`: ETS script paths compiled into the HAP. Paths must be under `<skill-name>/scripts/`; max 100 entries.
- `permissions`: Permissions required to call the skill. Use only when caller access must be gated.
- `requestPermissions`: Runtime permissions needed by ETS scripts. Non-ETS tool permissions belong to the platform tool definition.
- `minAPIVersion`: Minimum SDK API version generated from build settings when needed.
- `targetAPIVersion`: Target SDK API version generated from build settings when needed; must be >= `minAPIVersion`.