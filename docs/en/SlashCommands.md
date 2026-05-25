# Slash Commands Reference

This document categorizes commands by **parsing location**: `TUI Local Parsing` and `Gateway / Agent Parsing`.
For quick reference of current behavior; final implementation follows the code.

---

## Overview: By Parsing Side

### TUI Local Parsing (CLI Built-in)

Executed locally in the terminal UI, not through Gateway control pipeline.

| Command | Description |
|---|---|
| `/clear` | Clear screen |
| `/color` | Adjust TUI color scheme |
| `/copy` | Copy last message |
| `/exit` | Exit |
| `/help` | Show available commands |
| `/theme` | Switch theme |
| `/config` | Modify configuration (currently local, planned to unify with Gateway) |
| `/context` | Show context window usage and token breakdown (see below) |
| `/workspace` | Manage trusted directories (see below) |
| `/teamskills` | TeamSkills Hub publish/delete (`publish`/`delete`) |
| `/export` | Export current conversation to file or clipboard (see below) |
| `/status` | Show jiuwenswarm status overview, usage, config (see below) |
| `/statusline` | Configure the TUI footer status bar with a custom command (see below) |
| `/permissions` | Manage tool permissions (`allow`/`ask`/`deny`) |
| `/evolve` | Trigger skill self-evolution for one skill (see below) |
| `/evolve_list` | Show one skill's evolution records (see below) |
| `/evolve_simplify` | Simplify and consolidate one skill's evolution records (see below) |
| `/evolve_rebuild` | Rebuild `SKILL.md` from archives and evolution records (see below) |
| `/sandbox` | Set sandbox mode (see below) |
| `/auto-harness` | Auto-Harness task management (`run`/`schedule`, see below) |

> Note: `/mode` controlled switching logic is primarily on Gateway side, see "`/mode` and `/switch`" below.

### Gateway / Agent Parsing (Controlled Channel)

Identified by Gateway and forwarded to AgentServer and other backend capabilities.

| Command | Description |
|---|---|
| `/plan` | Switch to planning sub-mode |
| `/resume` | Resume historical session (see below) |
| `/new_session` | Create new session (IM only) |
| `/mode` | Mode switching (supports first-level entry and direct syntax) |
| `/switch` | Switch second-level mode within current mode family |
| `/skills` | Skills management (list, install, uninstall, marketplace) (see below) |
| `/model` | Model view, add, switch (see below) |
| `/mcp` | MCP server management (see below) |
| `/diff` | View session changes by turn (see below) |
| `/compact` | Compress current context (see below) |
| `/init` | Project initialization (see below) |
| `/branch` | Create a branch session from current conversation point (see below) |
| `/rewind` | Rewind conversation to before a specific turn (see below) |
| `/memory` | Memory management (see below) |
| `/cron` | Scheduled task (cron job) management (see below) |

---

## Key Command Details

### `/workspace` (TUI Trusted Directory Management)

Manages directories AI can access for file read, edit, and execute operations.

#### Subcommands

| Command | Description |
|---|---|
| `/workspace` or `/workspace get` | Show system default workspace and current trusted directories list |
| `/workspace add [path]` | Add trusted directory (defaults to cwd; error if path doesn't exist) |
| `/workspace set <path>` | Reset trusted dirs to single path (confirmation required if dirs exist) |
| `/workspace remove <path>` | Remove specified trusted directory |
| `/workspace clear` | Clear all trusted directories (use default workspace only) |

#### Concepts

- **System default workspace**: Fixed path `~/.jiuwenswarm/agent/jiuwenswarm_workspace`, always available
- **Trusted directories (`trusted_dirs`)**: User-authorized accessible directories, managed by TUI, passed to backend Agent

#### Control Logic

1. **Startup confirmation**: TUI prompts user whether to trust current directory
   - "Trust" → add current directory as trusted
   - "Don't trust" → use default workspace only

2. **Session-level management**: Trusted directories are effective for current CLI session, not persisted to file

3. **Backend passing**: TUI passes `trusted_dirs` via request params; Agent restricts file operations accordingly

4. **Path restriction**: Agent limits file operations within trusted directories; operations outside require user confirmation

5. **Path validation**: `add` and `set` validate path existence; error shown if invalid

#### Aliases

`/workspace_dir`, `/workspace-dir`

### `/mode` and `/switch` (Controlled Channel)

- First-level entry mapping:
  - `/mode agent` -> `agent.plan`
  - `/mode code` -> `code.normal`
  - `/mode team` -> `team`
- Direct syntax:
  - `/mode agent.plan` -> `agent.plan`
  - `/mode agent.fast` -> `agent.fast`
  - `/mode code.plan` -> `code.plan`
  - `/mode code.normal` -> `code.normal`
- Second-level switching:
  - agent family: `/switch plan` <-> `agent.plan`, `/switch fast` <-> `agent.fast`
  - code family: `/switch plan` <-> `code.plan`, `/switch normal` <-> `code.normal`
- Invalid combinations (e.g., `/switch fast` under `code.*`) return: `Invalid command`.
- Note: Standalone `/team` command removed, use `/mode team` instead.

### `/resume`

- `/resume list`: List historical sessions.
- `/resume <conversation_id>`: Resume specified session.

### `/model` (View / Add / Switch Model)

- Usage:
  - `/model` or `/model list`: List switchable models (with current model marker);
  - `/model <name>`: Switch to specified model;
  - `/model add <name> key=value ...`: Add model config (e.g., `model=...`, `provider=...`, `api_base=...`, `api_key=...`).
- Limitation: `video` / `audio` / `vision` cannot be set as default chat model via `/model <name>`, use `/config edit` or `/config set` instead.
- Config write behavior:
  - Adding model writes to `config.yaml` `models.defaults` (compatible with old structure), triggers Agent config reload;
  - Switching model validates config and environment variable placeholders, updates `MODEL_NAME` / `MODEL_PROVIDER` / `API_BASE` / `API_KEY`, writes back to `.env`.
- Secure display: Sensitive fields like `api_key`, `token` are masked.

### `/diff` (Session Change Review)

- Usage: `/diff` (no subcommands).
- Data source: TUI requests Agent diff service via `command.diff`, returns `turns` (change sets per turn) for current `session_id`.
- Display rules:
  - With changes: Shows `Found N turn(s) with file changes` with structured `turns`;
  - Without changes: Shows `No file changes in this session`.
- Scope: For viewing uncommitted per-turn change traces in current session, not a replacement for `git diff` full version control perspective.

### `/compact` (Context Compression)

- Usage: `/compact` (no parameters).
- Function: Trigger context compression,清理对话 history but keep summary in context.
- Data source: TUI requests Agent compression service via `command.compact`.
- Results:
  - `busy`: Compression in progress, retry later;
  - `compressed`: Success, shows before/after token counts and savings ratio;
  - `noop`: No compression needed, context already optimal.

### `/context` (Context Window Usage)

- Usage: `/context` (no parameters, no subcommands).
- Function: View the current session's context window occupancy and token usage details.
- Data source: TUI requests Agent context statistics service via `command.context`, carrying the current `mode`.
- Display contents:
  - **Overview panel**: Context window occupancy percentage + progress bar; `context_window` (used / limit tokens), `occupancy` (rate), `messages` (count);
  - **Token breakdown panel**: Shows token usage by `system_prompt`, `messages`, `tools`, and `total`;
  - **DeepAgent occupancy details** (if available): Key-value list of `context_occupancy` fields;
  - **DeepAgent usage details** (if available): Key-value list of `deepagent_usage` fields.
- Threshold warning: When occupancy >= 90%, the overview title shows `Context window 90% full — consider /compact`.
- Error handling: On request failure, displays `context failed: <error message>`.

### `/init` (Project Initialization)

- Usage: `/init` (no parameters).
- Function: Initialize project AI collaboration config, generates `JIUWENSWARM.md` and optionally `JIUWENSWARM.local.md`.
- Scope: Only runs in `code` mode.
- Flow:
  1. Select scope: `Team-shared` (JIUWENSWARM.md), `Personal` (JIUWENSWARM.local.md), or `Both`.
  2. Detect existing configs: Auto-detect `CLAUDE.md`, `.cursorrules`, `copilot-instructions.md` etc.
  3. Generate configs: Create project config files based on selection.
- Auto mode switch: If in `code.plan`, auto-switches to `code.normal` for write permission.

### `/mcp` (MCP Server Management)

- Usage:
  - `/mcp list`: List all MCP servers (name, transport, enabled status);
  - `/mcp show [name]`: Show MCP config; without `name` shows enabled items, with `name` shows one server detail;
  - `/mcp add --name <name> --transport <stdio|sse> ...`: Add a new MCP server;
  - `/mcp update --name <name> ...`: Update MCP server config (transport / params / enabled status);
  - `/mcp enable <name>`: Enable a specific MCP server;
  - `/mcp disable <name>`: Disable a specific MCP server;
  - `/mcp remove <name>`: Remove a specific MCP server.
- Transport parameters:
  - `stdio`: requires `--command`; optional `--args`, `--cwd`, `--env`;
  - `sse`: requires `--url`; optional `--headers`, `--timeout_s`.
- Examples:
  - `/mcp list`
  - `/mcp show`
  - `/mcp show playwright`
  - `/mcp add --name playwright --transport stdio --command python --args "server.py --transport stdio"`
  - `/mcp update --name playwright --transport sse --url http://127.0.0.1:9000/sse --headers "Authorization=Bearer xxx"`
  - `/mcp add --name local-sse --transport sse --url http://127.0.0.1:9000/sse`
  - `/mcp disable playwright`
  - `/mcp remove local-sse`
- Config and effect:
  - Changes are written to `config.yaml` under `mcp.servers`;
  - After write, Agent config reload is triggered, and runtime MCP server bindings are synced accordingly.

### `/evolve*` (Skill Self-Evolution)

These commands are registered and parsed by the TUI, then forwarded as slash text through the normal chat channel. The actual evolution logic runs on the Agent / Team backend:

- Agent mode: handled by `SkillEvolutionRail`; only `agent.plan` is supported.
- Team mode: handled by `TeamSkillEvolutionRail` for team skill evolution.
- Code mode and `agent.fast` do not support these commands.

#### Subcommands

| Command | Description |
|---|---|
| `/evolve <skill_name> [user_query]` | Trigger evolution for one skill. `agent.plan` scans the current conversation for tool failures and user corrections; Team mode requires `user_query`. |
| `/evolve_list <skill_name> [--sort score]` | Show one skill's evolution records with count, average score, usage/feedback stats, section, and content preview. |
| `/evolve_simplify <skill_name> [user_intent]` | Generate an approval-gated cleanup plan to merge duplicates, split long records, or remove low-value records. Trailing text is passed to the backend as intent. |
| `/evolve_rebuild <skill_name> [user_intent]` | Generate a rebuild follow-up prompt and continue as a normal Agent / Team task to rebuild `SKILL.md`. |

#### Approval Flow

- `/evolve` and `/evolve_simplify` do not silently write changes; the backend pushes a confirmation question and the TUI waits for approval.
- Accepting persists/solidifies the generated records; rejecting discards this generation.
- Accepted Team skill evolution syncs the team skill directory.
- While evolution or approval is pending, supplemental user input is queued and sent after evolution completes.

#### Examples

```bash
/evolve pptx improve export error handling
/evolve_list pptx --sort score
/evolve_simplify pptx merge duplicate export-failure records
/evolve_rebuild pptx strengthen Troubleshooting and Examples
```

### `/branch` (Branch Session)

- Usage: `/branch [name]`.
- Alias: `/fork`.
- Function: Create a branch session from the current conversation state, copying the current conversation history.
- Constraints:
  - Rejected when the session is busy (`session is busy`);
  - Rejected when the current session has no conversation records.
- Behavior:
  1. Generate a new `session_id` and send `session.fork` RPC to the backend (carrying `source_session_id`, `target_session_id`, and optional title).
  2. TUI automatically switches to the new branch session, clears the current transcript, and restores the branch history.
  3. Prompts the user that they are now in the new branch, and informs them they can use `/resume <original_session_id>` to return to the original session.
- Examples:
  - `/branch` — Create an untitled branch
  - `/branch fix-login-bug` — Create a branch named `fix-login-bug`

### `/rewind` (Rewind Conversation)

- Usage: `/rewind [turn_number]`.
- Alias: `/checkpoint`.
- Function: Rewind the current session to before a specified turn, supporting conversation-only, code-only, or both.
- Constraints:
  - Rejected when the session is busy (`session is busy`);
  - Rejected when there are no conversation turns.
- Interactive flow:
  1. Without parameters, displays a list of all conversation turns (with timestamps and file change statistics) for the user to select the target turn.
  2. After selecting, displays restore options:
     - **Restore conversation and code** — Truncate conversation and restore files to their prior state;
     - **Restore conversation only** — Only truncate conversation, files remain unchanged;
     - **Restore code only** — Only restore files, conversation remains unchanged (shown only when the target turn has file changes);
     - **Cancel** — Abort the operation.
  3. Calls the corresponding backend RPC based on selection:
     - `both` → `session.rewind_and_restore`
     - `conversation` → `session.rewind`
     - `code` → `session.restore_files`
- After rewind: TUI clears the transcript and reloads history; if the rewinded content contains user input, it is automatically filled into the input box.
- Limitation: Rewinding does not affect files edited manually or via bash commands.
- Examples:
  - `/rewind` — Interactive turn selection and restore mode confirmation
  - `/rewind 2` — Directly rewind to before turn 2

### `/memory` (Memory Management)

- Alias: `/mem`.
- Function: View and manage memory system status, memory files, toggle settings, and directory paths.
- Subcommands:

| Command | Description |
|---|---|
| `/memory` or `/memory edit` | Interactively select and edit a memory file (lists available files when no path is given) |
| `/memory list` | List all memory files (with size, line count, modification time) |
| `/memory edit <path>` | Open the specified memory file for editing (via `$EDITOR`) |
| `/memory status` | Show detailed memory system status |
| `/memory toggle [key]` | Toggle memory system switches (lists togglable items when no key is given) |
| `/memory open` | Show memory system directory paths |

- `status` display contents:
  - Current mode, storage engine, enabled status, proactive status, forbidden filter status;
  - Index status (FTS5, Vector, Cache), file count, chunk count;
  - Statistics for Project Memory, Coding Memory, Auto Memory, and External Memory.
- `toggle` available keys:
  - `memory_enabled` — Master memory switch;
  - `memory_proactive` — Proactive memory switch;
  - `memory_forbidden_enabled` — Forbidden filter switch.
  - After toggling, a prompt is shown if a session restart is required for the change to take effect.
- Examples:
  - `/memory` — Interactively edit a memory file
  - `/memory list` — List memory files
  - `/memory edit memory/MEMORY.md` — Edit a specific memory file
  - `/memory status` — View detailed status
  - `/memory toggle memory_enabled` — Toggle the master memory switch
  - `/memory open` — View memory directory paths

### `/cron` (Scheduled Task Management)

Manage cron jobs via RPC calls to the backend `CronController`, sharing the same backend logic and data store with the Web UI.

- Alias: `/crontab`
- Subcommands:

| Command | Description |
|---|---|
| `/cron` or `/cron list` | List all cron jobs |
| `/cron add name=<name> cron_expr=<expression> description=<desc> [other params]` | Create a new cron job |
| `/cron update <job_id> key=value ...` | Update specific fields of a job |
| `/cron delete <job_id>` | Delete a job |
| `/cron toggle <job_id> <on or off>` | Enable or disable a job |
| `/cron run <job_id>` | Run a job immediately |
| `/cron preview <job_id>` | Preview upcoming execution times for a job |

- `add` parameters:

| Parameter | Required | Description |
|---|---|---|
| `name` | Yes | Job name |
| `cron_expr` | Yes | Cron expression, supports two formats: 5-field (min hour day month dow) or 7-field Quartz (sec min hour day month dow year). 5-field is auto-converted to 7-field (second=0, year=*). Examples: daily 9am = `0 9 * * *` (5-field) or `0 0 9 * * ? *` (7-field) |
| `description` | Yes | Job description — the input prompt the Agent receives when executing |
| `targets` | No | Push channel, default `tui`; options: `tui`, `web`, `feishu`, `whatsapp`, `wecom`, `xiaoyi`, `wechat`, or `feishu_enterprise:<app_id>` |
| `timezone` | No | IANA timezone, default `Asia/Shanghai` |
| `mode` | No | Execution mode: `agent` (default, suitable for simple reminder-type tasks) or `plan` (for more complex reasoning tasks, allowing the Agent to plan the steps first before executing) |
| `wake_offset_seconds` | No | Wake-up offset in seconds, default 300 |
| `delete_after_run` | No | Auto-delete after one run, default false |

- `add` examples:
  - `/cron add name=minute-test cron_expr="0 * * * *" description="Tell me the current time" targets=tui`
  - `/cron add name=morning-brief cron_expr="0 9 * * *" description="Generate today's morning briefing" targets=tui mode=plan`
  - `/cron add name=reminder cron_expr="0 30 17 29 4 ? 2026" description="Don't forget the meeting" targets=tui delete_after_run=true`
  - `/cron add name=weekly-report cron_expr="0 9 * * 1" description="Generate weekly report" targets=web`

- `update` usage: Only pass the fields you want to change, e.g., `/cron update <id> name=new-name enabled=false`
- `list` display: sequence number, full job ID, name, cron expression, enabled status, description snippet
- `preview` display: wake_at and push_at timestamps for each upcoming execution

### `/skills` (Skills Management)

Manage skills lifecycle: listing, installing, uninstalling, and marketplace source management.

#### Subcommands

| Command | Description |
|---|---|
| `/skills` or `/skills list` | List skills (grouped: Installed / Available to install) |
| `/skills install <skill>` or `/skills install <skill@marketplace>` or `/skills install <path_or_url>` | Install a skill: builtin skills accept bare name, marketplace skills use `<name>@<marketplace>` format, local paths and remote URLs are auto-detected |
| `/skills uninstall <name>` | Uninstall a skill by name |
| `/skills marketplace` or `/skills marketplace list` | List marketplace sources (name, URL, enabled status, last updated) |
| `/skills marketplace add <name> <url>` | Add a new marketplace source |
| `/skills marketplace remove <name>` | Remove a marketplace source (also clears its cache) |
| `/skills marketplace toggle <name> <on or off>` | Enable or disable a marketplace source (`on`/`true`/`1` = enable, otherwise disable) |
| `/skills use <skill_name>, <query>` | Execute a query using a specific skill |

#### Concepts

- **Skill**: An extension capability that can be installed from marketplace sources, builtin directory, or local paths, providing additional functionality to the agent.
- **Builtin skill**: A preset skill shipped with the software. Install using bare skill name (e.g., `/skills install advanced-daily-report`); no marketplace source needed.
- **Marketplace source**: A remote repository (typically a Git URL) that hosts available skills. Each source has a name, URL, and enabled/disabled state.
- **Spec**: The install identifier format `<skill>@<marketplace>` used when installing from a marketplace; for builtin skills, omit `@` and the system auto-detects as `@builtin`.
- **Local install**: Use `/skills install <path>` to install from a local directory (must contain `SKILL.md`) or remote archive URL; paths/URLs are auto-detected and routed to the local import flow.
- **Install location**: The directory where a skill is stored after installation (`~/.jiuwenswarm/agent/jiuwenswarm_workspace/skills/`).
- **Source tag**: Each skill in the list is tagged with its source: `[builtin]` = builtin, `[local]` = imported, `[project]` or marketplace name = other.

#### Grouped List Display

`/skills list` returns skills in two groups:

1. **Installed**: Skills already in the user's skills directory, ready to use.
2. **Available to install**: Builtin skills not yet installed, plus marketplace skills available for installation. Use `/skills install` first.

#### IM vs TUI Differences

Both ultimately request `skills.list`, but trigger methods and display differ.

| Side | Trigger Method | Behavior |
|---|---|---|
| IM (Feishu etc. controlled channel) | Exact match `/skills list` (whitespace normalized first) | Gateway intercepts control message and requests `skills.list`, results shown as IM notification/card; standalone `/skills` doesn't go through this control path. |
| TUI (CLI built-in) | Input `/skills` | Locally executes built-in command and calls `skills.list`, displays as grouped list view in session (titles `Installed Skills` and `Available Skills`); shows `No installed skills` when empty. |

For other subcommands (`/skills install`, `/skills uninstall`, `/skills marketplace add/remove/toggle`, `/skills use`), Gateway does **not** intercept them — on the IM side they are treated as regular chat messages. These subcommands are only functional on the TUI (CLI built-in) and Web UI paths, where they send RPC requests directly to AgentServer.

#### Notes

- **Timeout**: `install`, `uninstall`, and `marketplace toggle` requests have a 120-second timeout on the TUI side; other subcommands have no explicit timeout.
- **Builtin auto-detection**: When installing with `/skills install <skill>` (no `@`), the system checks if it matches a builtin skill and redirects to the builtin install flow; if not, a format hint is returned.
- **Path/URL auto-detection**: When installing with `/skills install <path>` (local path like `/path/to/skill` or `C:\skill`, or remote URL `https://...`), the system automatically routes to the local import flow.
- **Cache cleanup**: `marketplace remove` sends `{ name, remove_cache: true }` to also clear the local cache for that source.
- **Auto-refresh**: `marketplace add`, `marketplace remove`, and `marketplace toggle` automatically re-list marketplace sources after a successful operation.
- **Offline handling**: `/skills use` checks connection status; if offline, shows `offline: waiting for reconnect before sending /skills use request`.

#### Examples

- `/skills` — List skills (grouped: Installed / Available)
- `/skills list` — List skills (explicit subcommand)
- `/skills install advanced-daily-report` — Install a builtin skill (bare name auto-detect)
- `/skills install advanced-daily-report@builtin` — Install a builtin skill (explicit format)
- `/skills install my-skill@marketplace` — Install a skill from marketplace
- `/skills install /path/to/my-skill` — Install a skill from local directory
- `/skills install https://example.com/skill.zip` — Install a skill from remote URL
- `/skills uninstall my-skill` — Uninstall a skill
- `/skills marketplace list` — List marketplace sources
- `/skills marketplace add community https://github.com/user/skills-repo` — Add a marketplace source named "community"
- `/skills marketplace remove community` — Remove the "community" marketplace source
- `/skills marketplace toggle community on` — Enable the "community" marketplace source
- `/skills marketplace toggle community off` — Disable the "community" marketplace source
- `/skills use my-skill, Code and execute a Hello World program.` — Use a skill to execute a query

### `/export` (Export Conversation)

Export the current conversation to a file or clipboard.

#### Usage

- `/export` — Copy conversation to clipboard (no filename argument)
- `/export <filename>` — Save conversation to a `.txt` file in workspace directory

#### Subcommands

| Command | Description |
|---|---|
| `/export` | Copy entire conversation to clipboard; if clipboard unavailable, prompt to specify a filename |
| `/export <filename>` | Write conversation to `filename.txt` in workspace directory; if filename lacks `.txt` extension, it is automatically appended |

#### Output Format

The exported text renders each conversation entry with a timestamp and role prefix:

- `[User] <timestamp>` — User input
- `[Assistant] <timestamp>` — Assistant response
- `[Thinking] <timestamp>` — Internal reasoning trace
- `[Tools] <timestamp>` — Tool calls with name, summary, and truncated result (max 500 chars)
- `[System] / [Error] / [Info] <timestamp>` — System messages
- `[Diff] <timestamp>` — Per-turn file change summary

#### Tab Completion

When typing `/export ` and pressing Tab, auto-generated filename suggestions appear:

- `<timestamp>-<sanitized-first-prompt>.txt` — Based on the first user message (truncated to 50 chars, sanitized)
- `conversation-<timestamp>.txt` — Generic timestamped name

Timestamp format: `YYYY-MM-DD-HHmmss`.

#### Behavior Details

- **Clipboard fallback**: If no filename is given and clipboard is unavailable, an error message prompts the user to specify a filename instead.
- **Filename normalization**: Any extension is replaced with `.txt`; e.g., `/export my-chat.json` becomes `my-chat.txt`.
- **Write location**: Files are saved to `ctx.getWorkspaceDir()` (or `process.cwd()` as fallback).

#### Examples

- `/export` — Copy conversation to clipboard
- `/export my-chat` — Save to `my-chat.txt` in workspace
- `/export 2026-05-09-debug-session.txt` — Save with explicit timestamp name

### `/sandbox` (Sandbox Mode Management)

Enter / leave jiuwenbox sandbox mode and tune its runtime policy. Calls `command.sandbox` on the agent server.

#### Subcommands

| Command | Description |
|---|---|
| `/sandbox` or `/sandbox status` | Show current runtime (`enabled`, `excluded_commands`, `files.allow_write`, `files.deny_write`) |
| `/sandbox enable` | Enter sandbox mode (spawns jiuwenbox if needed, rebuilds agent) |
| `/sandbox disable` | Leave sandbox mode (rebuilds agent; stops jiuwenbox only if jiuwenswarm started it) |
| `/sandbox exclude add <pattern>` | Add a shell glob whose matches run locally instead of in the sandbox |
| `/sandbox exclude remove <pattern>` | Remove a pattern |
| `/sandbox exclude list` | List current `excluded_commands` |
| `/sandbox files allow <path> [perm]` | Allow writing `<path>` inside the sandbox |
| `/sandbox files deny <path>` | Deny writing `<path>` inside the sandbox |
| `/sandbox files remove <path>` | Remove `<path>` from the user-configured allow & deny sets |
| `/sandbox files list` | List effective `allow_write` / `deny_write` |
| `/sandbox help` | Print usage |

#### Concepts

- **Platform support**: `/sandbox` is Linux-only (jiuwenbox depends on Linux kernel features such as bwrap, Landlock, and Linux namespaces). On a Windows or macOS agent-server, every `/sandbox` sub-command returns a `SANDBOX_BAD_REQUEST` error. If the TUI runs on Windows/macOS but the agent-server is on a Linux host, the command works — what matters is the agent-server's platform.
- **Effective write policy**: `files.allow_write` / `files.deny_write` in the status panel show the merged view of auto-managed and user-configured entries. Auto-managed entries are server-injected (intrinsic files such as `AGENT.md`, `HEARTBEAT.md`, `IDENTITY.md`, `SOUL.md`, `USER.md`, the `memory/daily_memory/` directory, and depending on the mode, `project_dir` and `config/config.yaml`) and cannot be removed via `/sandbox files remove`.
- **preserve_file_sharing_mode**: Controlled by jiuwenswarm config, not by `/sandbox`. Only `mount` is supported: intrinsic files and `project_dir` are bind-mounted into the sandbox and `project_dir/config/config.yaml` is explicitly added to `deny_write`. Writing any other value into config.yaml is rejected by the server.
- **excluded_commands**: Match the full command string (not just `argv[0]`); a match makes that tool call run on the host, effectively granting the command's side effects to the local environment.
- **Add / remove are strict**: `exclude add` rejects a pattern that is already in the list; `exclude remove` rejects a pattern that is not in the list. `files allow|deny` rejects a path that is already in the same bucket, and rejects a path that exists in the opposite bucket (allow vs deny conflict) — run `files remove` first if you want to flip it. `files remove` rejects paths that have no matching user-configured entry.
- **enable / disable**: Triggers an agent rebuild. The response lists `rebuilt_modes` (typically `agent.*` / `code.*`) and the jiuwenbox endpoint.

#### Examples

- `/sandbox enable` — turn on sandbox mode
- `/sandbox status` — see runtime + effective files
- `/sandbox files allow ./tmp/ 0777` — let the sandbox write into `./tmp/` with mode 0777
- `/sandbox exclude add "git *"` — let `git` run on the host instead of inside the sandbox

### `/status` (Show Status)

Display jiuwenswarm runtime status: overview, usage statistics, or config editor.

#### Usage

- `/status` or `/status overview` — Show core identity, model/API info, MCP servers, and config sources
- `/status usage` — Show session token usage statistics
- `/status config` — Enter interactive config editor

#### Subcommands

| Command | Description |
|---|---|
| `/status` | Show full status overview (version, session, model, connection, MCP servers, config) |
| `/status overview` | Same as `/status` — explicit overview subcommand |
| `/status usage` | Show session token usage (input, output, total, per-model breakdown) |
| `/status config` | Enter interactive config editor (same as `/config edit`) |

#### Overview Display Sections

When `/status` is run, four key-value panels are displayed:

1. **Core identity**: version, session ID, session name (or prompt to `/rename`), cwd, current mode
2. **Model & API**: model name, provider, API base URL, connection status
3. **MCP servers**: each server's name, transport type, and enabled/disabled state
4. **Config sources**: config file path and all settings source paths

#### Usage Display

`/status usage` shows token consumption for the current session:

- Total input tokens, output tokens, and total tokens
- Per-model breakdown: model name, token count, input/output split

#### Interactive Mode

If the TUI provides an interactive StatusView (`ctx.enterStatusView`), `/status` opens the full status UI with tabs. The subcommand argument selects the initial tab:

- `/status` → opens on overview tab
- `/status usage` → opens on usage tab
- `/status config` → opens on config tab

If StatusView is unavailable, the command falls back to inline key-value display.

#### Data Sources

- Overview data: `command.status` RPC request to AgentServer
- Usage data: `ctx.getUsageSummary()` from local session tracking
- Config data: `config.get` RPC request to AgentServer

#### Examples

- `/status` — Show full overview
- `/status overview` — Show overview (explicit)
- `/status usage` — Show token usage
- `/status config` — Open config editor

### `/statusline` (TUI Footer Status Bar)

Configure the TUI footer status bar with a custom shell command that dynamically displays session info (mode, model, cwd, etc.), modeled after Claude Code's `/statusline` implementation.

#### Subcommands

| Command | Description |
|---|---|
| `/statusline` or `/statusline get` | View current status line configuration |
| `/statusline set <shell-command>` | Set the status line command (its output will appear in the TUI footer) |
| `/statusline clear` | Remove the status line configuration (footer bar will hide) |
| `/statusline help` | Show the JSON input fields reference |

#### Concepts

- **StatusLine**: A one-line text area at the bottom of the TUI that displays user-defined dynamic information. When a custom statusline is configured, the built-in status line is automatically hidden to avoid redundant information.
- **Shell command**: The configured shell command is automatically executed every 2 seconds; its stdout output is rendered as the status bar text.
- **JSON input**: Each execution receives current session info as JSON, which can be parsed with `jq` or other tools. On POSIX (Linux/macOS), JSON is passed via stdin pipe; on Windows, due to MSYS2 pipe inheritance limitations, the system automatically writes JSON to a temp file and replaces `$(cat)` in the command with `$(cat "filepath")` — the user doesn't need to modify their command format.
- **Prerequisites**: Requires `jq` (https://stedolan.github.io/jq/) for JSON parsing; Windows users also need to add Git Bash's `usr\bin` directory to the system PATH (e.g., `E:\Git\usr\bin`).

#### JSON Input Fields

The command receives the following JSON data on each execution:

| Field | Description |
|---|---|
| `session_id` | Current session ID |
| `session_name` | Session title (set via `/rename`) |
| `cwd` | Current working directory |
| `mode` | Current mode (`agent.plan` / `agent.fast` / `code.plan` / `code.normal` / `team`) |
| `model` | Current model name |
| `provider` | Model provider |
| `version` | jiuwenswarm version |
| `connection` | Connection status (`idle` / `connecting` / `connected` / `reconnecting` / `auth_failed`) |
| `theme` | Current theme name |
| `accent_color` | Current accent color name |
| `transcript_mode` | Transcript display mode (`compact` / `detailed`) |
| `transcript_fold_mode` | Fold mode (`none` / `tools` / `thinking` / `all`) |
| `is_processing` | Whether agent is working (`true` / `false`) |
| `is_paused` | Whether paused (`true` / `false`) |
| `is_interrupted` | Whether interrupted (`true` / `false`) |
| `cancellable_work` | Whether there is running work (`true` / `false`) |
| `streaming_state` | Streaming state (`idle` / `streaming` / `tool_call` / `tool_result`) |
| `last_error` | Last error message or `null` |
| `evolution_status` | Evolution status (`idle` / `running`) |
| `active_subtask_count` | Number of active subtasks |
| `todo_count` | Number of todo items |
| `usage.total_input_tokens` | Total input tokens for session |
| `usage.total_output_tokens` | Total output tokens for session |
| `usage.total_tokens` | Total tokens for session |

#### Command Writing Template

Use the following template to write commands. `input=$(cat)` reads JSON into a variable, then `echo "$input" | jq -r .field` extracts each field. `// "default"` is jq's fallback syntax — when a field is null or empty, the default value is used.

**General formula**:

```
/statusline set 'input=$(cat); field1=$(echo "$input" | jq -r '.field1 // "default"'); field2=$(echo "$input" | jq -r '.field2 // "default"'); echo "format string"'
```

**Recommended universal command** (shows mode, model, tokens, connection):

```
/statusline set 'input=$(cat); mode=$(echo "$input" | jq -r '.mode // "?"'); model=$(echo "$input" | jq -r '.model // "?"'); tokens=$(echo "$input" | jq -r '.usage.total_tokens // 0'); conn=$(echo "$input" | jq -r '.connection // "?"'); echo "$mode | $model | tokens:$tokens | $conn"'
```

**Field extraction quick reference**:

| Field to display | jq syntax |
|---|---|
| Session name | `jq -r '.session_name // ""'` |
| Working directory | `jq -r '.cwd // "?"'` |
| Mode | `jq -r '.mode // "?"'` |
| Model name | `jq -r '.model // "?"'` |
| Provider | `jq -r '.provider // "?"'` |
| Version | `jq -r '.version // "?"'` |
| Connection | `jq -r '.connection // "?"'` |
| Is processing | `jq -r '.is_processing // false'` |
| Is paused | `jq -r '.is_paused // false'` |
| Streaming state | `jq -r '.streaming_state // "idle"'` |
| Last error | `jq -r '.last_error // ""'` |
| Evolution status | `jq -r '.evolution_status // "idle"'` |
| Subtask count | `jq -r '.active_subtask_count // 0'` |
| Todo count | `jq -r '.todo_count // 0'` |
| Total input tokens | `jq -r '.usage.total_input_tokens // 0'` |
| Total output tokens | `jq -r '.usage.total_output_tokens // 0'` |
| Total tokens | `jq -r '.usage.total_tokens // 0'` |

#### More Examples

- `/statusline` — View current configuration
- `/statusline set 'input=$(cat); model=$(echo "$input" | jq -r .model); echo "$model"'` — Show model name only
- `/statusline set 'input=$(cat); proc=$(echo "$input" | jq -r .is_processing); model=$(echo "$input" | jq -r .model); echo "$proc | $model"'` — Show processing state and model
- `/statusline set 'input=$(cat); err=$(echo "$input" | jq -r .last_error); if [ "$err" != "null" ] && [ "$err" != "" ]; then echo "error: $err"; else echo "ok"; fi'` — Show error when present, otherwise "ok"
- `/statusline clear` — Remove status line configuration
- `/statusline help` — View JSON input fields reference

#### Behavior Details

- **Poll frequency**: The configured command runs every 2 seconds automatically.
- **Timeout protection**: Individual executions timeout after 3 seconds; no impact on subsequent polls.
- **Output limit**: Command output over 10KB is truncated; display width auto-fits the TUI terminal width.
- **Failure silence**: Command execution failures don't show errors; previous successful output is kept or the bar hides.
- **Persistence**: Configuration is saved in `~/.jiuwenswarm-tui/config.json` under the `statusLine` field; restored on TUI restart.
- **Alias**: `/sl`
- **Windows adaptation**: The system automatically replaces `$(cat)` with reading from a temp file; the user's command format remains unchanged. Git Bash's `usr\bin` must be in the system PATH.

#### Config File Structure

```json
{
  "statusLine": {
    "type": "command",
    "command": "input=$(cat); mode=$(echo \"$input\" | jq -r '.mode // \"?\"'); model=$(echo \"$input\" | jq -r '.model // \"?\"'); tokens=$(echo \"$input\" | jq -r '.usage.total_tokens // 0'); echo \"$mode | $model | tokens:$tokens\"",
    "padding": 0
  }
}
```

### `/auto-harness` (Auto-Harness Task Management)

Manage Auto-Harness task creation, execution, and monitoring. Auto-Harness generates harness extension packages via automated pipelines, supporting two pipeline types:

- **optimize_expert_harness** (backend value `extended_evolve_pipeline`): Generate a local harness extension package
- **optimize_meta_harness** (backend value `meta_evolve_pipeline`): Submit PR (requires git config)

During pipeline execution, extension packages are **activated automatically by default** — no manual user confirmation is needed. Logs display `harness.extension_ready` (extension ready, showing directory and component info) and `harness.activate_interaction` (activation confirmation prompt) events.

#### Configuration Requirements

Using the `optimize_meta_harness` pipeline requires the following fields to be configured (via `/config edit` or `/status config`):

| Field | Required | Description |
|---|---|---|
| `git.user_name` | Yes | Git commit username |
| `git.user_email` | Yes | Git commit email |
| `git.fork_owner` | Yes | Fork repository owner (e.g., `SnapeK`) |
| `gitcode.access_token` | No | GitCode API token (can also be provided via environment variable `GITCODE_ACCESS_TOKEN`) |

If configuration is incomplete, the task creation will prompt the missing fields.

#### Subcommands

| Command | Description |
|---|---|
| `/auto-harness run [--pipeline <pipeline>] <query>` | Execute a one-time Auto-Harness task |
| `/auto-harness schedule start --interval <hours> [--pipeline <pipeline>] <query>` | Create a scheduled task |
| `/auto-harness schedule list` | List all tasks |
| `/auto-harness schedule status <task_id>` | View task details |
| `/auto-harness schedule logs <task_id> [--history <n>]` | View task execution logs |
| `/auto-harness schedule cancel <task_id>` | Cancel a task |
| `/auto-harness schedule delete <task_id>` | Delete a task |

#### `/auto-harness run` (One-time Execution)

- Usage: `/auto-harness run [--pipeline <pipeline>] <query>`
- Flow:
  1. If pipeline is not specified, interactively select the pipeline type
  2. If `optimize_meta_harness` is selected, automatically check git config completeness
  3. Create and execute a one-time task
  4. Automatically enter real-time log streaming mode (similar to `tail -f`)
- Examples:
  - `/auto-harness run Optimize database query performance` — No pipeline specified, interactive selection
  - `/auto-harness run --pipeline optimize_expert_harness Optimize context compression` — Specify pipeline

#### `/auto-harness schedule start` (Create Scheduled Task)

- Usage: `/auto-harness schedule start --interval <hours> [--pipeline <pipeline>] <query>`
- Parameters:
  - `--interval` / `-i` (required): Execution interval in hours; options: `1`, `2`, `4`, `8`, `12`, `24`
  - `--pipeline` / `-p` (optional): Pipeline type; interactively selected if not specified
  - `<query>` (required): Optimization target description
- Flow:
  1. If pipeline is not specified, interactively select
  2. If `optimize_meta_harness` is selected, check git config
  3. Interactively confirm whether to run immediately
  4. Create the scheduled task
- Examples:
  - `/auto-harness schedule start --interval 4 Optimize context compression`
  - `/auto-harness schedule start -i 2 -p optimize_meta_harness Submit database optimization PR`

#### `/auto-harness schedule logs` (View Execution Logs)

- Usage: `/auto-harness schedule logs <task_id> [--history <n>]`
- Modes:
  - Default: Stream current running logs in real-time (`tail -f` mode); Ctrl+C to interrupt
  - `--history <n>`: View historical execution logs (`view` mode, `n` is the history index, 0 = most recent)

---

## Planned Features

| Command | Description |
|---|---|
| `/btw` | Ask question |
| `/memory` | Memory management |
| `/export` | Export related files |
| `/permissions` | Permission management |
