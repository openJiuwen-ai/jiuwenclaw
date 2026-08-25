# Automatic Approval

Automatic Approval reduces repeated tool-confirmation prompts in single-agent work. For **each individual tool call**, it considers the current task and your existing permission configuration, then chooses to:

- allow that call once;
- deny that call; or
- ask you to decide manually.

Automatic Approval does not permanently unlock a tool and cannot override an explicit denial.

## Where it applies

| Scenario | Automatic Approval | Notes |
| --- | --- | --- |
| Web single-agent work mode | Supported | This is the primary user entry point. |
| Code Agent and Code plan | Supported | Plan write restrictions and plan approval remain separate safeguards. |
| Teams, clusters, and distributed teams | Not supported | This matches existing tool-permission approval support. Team Automatic Approval will be added after tool-permission support is available. |

“Not supported” in this table means that Automatic Approval does not currently provide a behavior guarantee for that scenario; it does not mean the mode itself is unavailable.

An Automatic Approval session is bound to one primary project/workspace. Start a new session when switching to a different directory.

## How it differs from other permission profiles

The permission selector near the Web input provides these profiles:

| Profile | Behavior |
| --- | --- |
| **Default** | Existing tool and path rules execute, ask, or deny. A Permission card appears when confirmation is required. |
| **Automatic Approval** | Within existing policy limits, each call is allowed once, denied, or escalated to you. |
| **Full Access** | Disables the tool permission boundary and no longer asks for approval. It carries substantially more risk and is not Automatic Approval. |

A profile change takes effect on the **next conversation turn**.

## How to use it

### Enable it in the WebUI

1. Open a single-agent work session.
2. Select the permission control near the input area.
3. Choose **Automatic Approval**.
4. Continue with your task from the next turn.

When a call still needs human judgment, JiuwenSwarm shows the normal Permission card. You can allow it once, deny it, or use an existing option such as remembering the choice for the session.

### Enable it in configuration

The configuration file is normally `~/.jiuwenswarm/config/config.yaml`. If `JIUWENSWARM_CONFIG_DIR` is set, use `config.yaml` in that directory.

Minimum configuration:

```yaml
permissions:
  enabled: true
  mode: auto
```

Return to the Default profile:

```yaml
permissions:
  enabled: true
  mode: manual
```

`permissions.mode` selects the `manual` or `auto` runtime profile. Do not confuse it with `permissions.permission_mode`, described below.

## Existing settings that affect Automatic Approval

Automatic Approval operates inside the existing tool-permission boundary. The following settings directly affect its result.

### Tool and argument rules

```yaml
permissions:
  schema: tiered_policy
  permission_mode: normal
  defaults:
    "*": ask
  tools:
    bash: ask
    write_file: allow
  rules:
    - id: deny-dangerous-command
      tools: [bash]
      pattern: "rm *"
      action: deny
```

- `permissions.tools` sets tool-level `allow`, `ask`, or `deny` behavior.
- `permissions.rules` adds more specific command, path, or argument rules.
- `permissions.defaults` is used when nothing else matches.
- `permissions.permission_mode` is `normal` or `strict` and changes severity mapping for rules without an explicit `action`.
- `builtin_rules.yaml` beside the main configuration supplies built-in security rules.

An explicit `deny` always wins. Automatic mode also does not treat a broad default `allow` as unrestricted authority; high-risk or highly flexible tools may still be reviewed or escalated to you.

### Path and workspace rules

```yaml
permissions:
  file_guard:
    enabled: true
    defaults:
      read: ask
      write: ask
      exec: ask
    workspace:
      read: allow
      write: allow
      exec: ask
    paths:
      - path: "/data/shared"
        read: allow
        write: ask
        exec: deny
```

- `file_guard` controls read, write, and execute access for the workspace, trusted directories, and external paths.
- More specific path-level `ask` and `deny` rules continue to restrict Automatic Approval.
- `external_directory` is retained for legacy compatibility; use `file_guard` for new configuration.
- Directories trusted through `/add-dir` continue to follow the existing path rules.

### Remembered approvals and channel rules

- `permissions.approval_overrides` and session-remembered choices may reduce later prompts within their existing scope, but cannot override explicit denials.
- `permissions.owner_scopes` continues to control tools for particular channels and users. It does not add Automatic Approval to Teams or other unsupported modes.

### AutoReviewer options

Most users do not need to change these options:

```yaml
permissions:
  auto:
    reviewer_timeout_ms: 60000
    reviewer_min_confidence: 0.7
    persistent_audit_enabled: false
    bounded_write_max_files: 3
    bounded_write_excluded_paths: []
```

| Setting | Effect |
| --- | --- |
| `reviewer_timeout_ms` | Time allowed for automatic review. A timeout escalates to you and never becomes an automatic allow. |
| `reviewer_min_confidence` | Minimum confidence. Lower-confidence results are escalated. |
| `persistent_audit_enabled` | Enables persistent approval audit records. |
| `bounded_write_max_files` | Maximum number of files eligible for the bounded-write fast path. |
| `bounded_write_excluded_paths` | Additional sensitive paths excluded from that fast path; these are merged with built-in protected paths. |

AutoReviewer uses the currently configured model service, so an eligible tool call may add one model request and a small amount of latency. If the model is unavailable, times out, or returns an invalid result, the call is escalated or denied; it is never silently allowed.

## What you will see

- Automatically allowed: the tool continues and an Automatic Approval badge or reason is shown.
- Escalated: a normal Permission card asks you to decide.
- Denied: the tool does not run and a denial reason is shown.
- Multiple pending calls: cards are handled one at a time in order; there is no “allow all” action.

## Known display limitation

For tasks with multiple subagents, the Web todo panel may show one item as running and the others as pending even while several subagents are actually running concurrently. This is an existing todo-status projection issue, not serialization caused by Automatic Approval, and it does not mean child agents have inherited Automatic Approval.

## Safety recommendations

- Prefer Automatic Approval instead of keeping Full Access enabled to avoid prompts.
- Keep `ask` or `deny` for shell commands, external paths, and sensitive files.
- Use `file_guard` to define workspace and external-directory boundaries.
- Validate rule changes on a new turn, and start a new session when changing workspaces.
- Periodically review remembered approvals and `approval_overrides`.

See [Tool Permissions & Security](ToolPermissionsSecurity.md) and [Configuration](Configuration.md) for related configuration details.
