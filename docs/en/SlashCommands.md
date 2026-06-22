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
| `/keybindings` | View, edit, or reset TUI keyboard shortcuts (alias `/keybind`) |
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
| `/hooks` | Browse configured hooks (read-only, see below) |
| `/sandbox` | Set sandbox mode (see below) |
| `/agents` | Manage Agent configs (list, get, create, update, enable, disable, delete, see below) |
| `/auto-harness` | Auto-Harness task management (`run`/`schedule`/`issue`, see below) |

> Note: `/mode` controlled switching logic is primarily on Gateway side, see "`/mode` and `/switch`" below. The TUI local command additionally supports `/mode plan` and `/mode team.normal`; see the TUI guide for details.

### Gateway / Agent Parsing (Controlled Channel)

Identified by Gateway and forwarded to AgentServer and other backend capabilities.

| Command | Description |
|---|---|
| `/plan` | Switch to planning sub-mode |
| `/resume` | Resume historical session (see below) |
| `/new_session` | Create new session (IM only) |
| `/mode` | Mode switching (supports first-level entry and direct syntax) |
| `/switch` | Switch second-level mode within current mode family |
| `/skills` | Skills management (list, install, uninstall, marketplace, ClawHub, SkillNet) (see below) |
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
  - `/mode code.normal` -> `code.normal`
  - `/mode code.team` -> `code.team`
- Second-level switching:
  - agent family: `/switch plan` <-> `agent.plan`, `/switch fast` <-> `agent.fast`
  - code family: `/switch normal` <-> `code.normal`, `/switch team` <-> `code.team`
- Invalid combinations (e.g., `/switch fast` under `code.*`) return: `Invalid command`.
- Controlled channels do not accept `/mode plan` or `/mode team.normal`.
- Note: Standalone `/team` command removed, use `/mode team` instead.

### `/resume`

- `/resume list`: List historical sessions.
- `/resume <conversation_id>`: Resume specified session.

#### Interactive picker (TUI)

Entering **`/resume`** or **`/continue`** with **no arguments** opens an interactive session picker (instead of a plain `session.list` dump).

| Key | Action |
| --- | --- |
| `↑` / `↓` | Move focus between sessions |
| `Enter` | Resume the focused session |
| type chars | Live search (filter by session ID / title / project dir) |
| `Backspace` | Delete a search character |
| `Space` | Preview the focused session info card (title, ID, project dir, branch, message count, last active / created). In preview: `Enter` resumes, `Space`/`Esc` goes back |
| `Ctrl+R` | Rename the focused session. In edit mode: `Enter` saves, `Esc` cancels, empty value clears the title |
| `Ctrl+A` | Toggle scope between "all projects" and "current project only" |
| `Ctrl+B` | Toggle git branch filter (only show sessions whose `git_branch` strictly equals the current project's branch) |
| `Esc` | Clear search if any; otherwise close the picker |

> `Space` / `Ctrl+R` / `Ctrl+A` / `Ctrl+B` / `Esc` in the list can be rebound under the `ResumeList` context via `/keybindings` (preview/rename sub-states and search text entry stay hardcoded).

Behavior:

- **Defaults to listing all projects** (press `Ctrl+A` to narrow to the current project). When the current project has no sessions, an (empty) picker still opens so you can press `Ctrl+A`.
- **Branch recording & filtering (`Ctrl+B`)**: a session's git branch is recorded (per its `project_dir`) on the first message (`HEAD` for non-git/detached). When the filter is on, sessions are matched by branch **name** strictly; legacy sessions without a recorded branch and `HEAD` sessions are filtered out. Note the match is by name only and not repo-aware — with "all projects + branch filter" enabled, same-named branches in different directories are shown together.
- **Restore scope**: resume only restores the **conversation context** (history, session ID, accent color, workflow snapshot, window title); it does **not** switch the workspace / current working directory.

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

### `/diff` (Interactive Change Review)

- Usage: `/diff` (no subcommands).
- Data source: TUI requests Agent diff service via `command.diff`, returns `turns` (change sets per turn) and `gitDiff` (uncommitted working tree changes) for current `session_id`.
- Display mode: Opens a **full-screen interactive Diff viewer**:
  - **List view**: Shows all changed files (working tree `working` and per-turn `Turn N`) with relative paths, source label, and added/removed line counts;
  - **Detail view**: Press `Enter` on a selected file to view its full hunk-by-hunk diff with scrolling support.
- List view keybindings:
  - `↑` / `↓` — Move selection (auto-scrolls);
  - `Enter` — View full diff for the selected file;
  - `Home` / `g` — Jump to top;
  - `End` / `Shift+g` — Jump to bottom;
  - `Esc` / `Ctrl+C` — Close.
- Detail view keybindings:
  - `↑` / `↓` — Scroll line by line;
  - `PgUp` / `PgDn` — Page up / down;
  - `Home` / `g` — Go to file top;
  - `End` / `Shift+g` — Go to file bottom;
  - `←` / `Esc` — Return to list view.
- Scope: Covers both the working tree (`git diff HEAD`) and per-turn change traces. Not a replacement for `git diff` full version control perspective.
- Fallback: When the TUI does not provide the `enterDiffViewer` capability, falls back to inline display (file names, source, and line stats only).

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
- Auto mode switch: Code initialization runs in `code.normal` for write permission.

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
| `/cron show <job_id>` | Show detailed info for a specific job |
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
| `targets` | No | Push channel, default `tui`; options: `tui`, `web`, `feishu`, `whatsapp`, `wecom`, `xiaoyi`, `wechat`, `dingtalk`, or `feishu_enterprise:<app_id>`. With `targets=tui`, results broadcast to all connected TUI windows; see [Scheduled tasks — Push to TUI](ScheduledTasks.md#5-push-to-the-tui-channel) |
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
- `show` display: full job details in key-value format (id, name, status, cron_expr, timezone, description, targets, mode, wake_offset_seconds, delete_after_run)
- `list` display: sequence number, full job ID, name, cron expression, enabled status, description snippet
- `preview` display: wake_at and push_at timestamps for each upcoming execution

### `/skills` (Skills Management)

Manage skills lifecycle: listing, installing, uninstalling, marketplace source management, ClawHub and SkillNet online skill registries.

#### Subcommands

| Command | Description |
|---|---|
| `/skills` or `/skills list` | List skills (grouped: Installed / Available to install) |
| `/skills install <skill>` or `/skills install <slug@clawhub>` or `/skills install <name@skillnet>` or `/skills install <skill@marketplace>` or `/skills install <path_or_url>` | Install a skill: builtin accepts bare name, ClawHub uses `<slug>@clawhub`, SkillNet uses `<name>@skillnet` (auto-searches for URL), marketplace uses `<name>@<marketplace>`, local paths and URLs auto-detected |
| `/skills uninstall <name>` | Uninstall a skill by name |
| `/skills marketplace` or `/skills marketplace list` | List marketplace sources (name, URL, enabled status, last updated) |
| `/skills marketplace add <name> <url>` | Add a new marketplace source |
| `/skills marketplace remove <name>` | Remove a marketplace source (also clears its cache) |
| `/skills marketplace toggle <name> <on or off>` | Enable or disable a marketplace source (`on`/`true`/`1` = enable, otherwise disable) |
| `/skills marketplace clawhub` | View ClawHub token status (configured/not configured) |
| `/skills marketplace clawhub token <value>` | Set ClawHub CLI token |
| `/skills marketplace clawhub token` | View ClawHub token status |
| `/skills skillnet` or `/skills skillnet search <query>` | Search SkillNet skill registry (shows name, description, author, stars, category, URL) |
| `/skills skillnet install <skill_url>` | Install a skill from SkillNet by URL (async download, auto-polls progress) |
| `/skills use <skill_name>, <query>` | Execute a query using a specific skill |

#### Concepts

- **Skill**: An extension capability that can be installed from marketplace sources, ClawHub, SkillNet, builtin directory, or local paths, providing additional functionality to the agent.
- **Builtin skill**: A preset skill shipped with the software. Install using bare skill name (e.g., `/skills install advanced-daily-report`); no marketplace source needed.
- **ClawHub**: An online skill registry ([clawhub.ai](https://clawhub.ai)) hosting community-published skills. Install using `<slug>@clawhub` format, where slug is the skill's unique identifier (not its display name). Requires a ClawHub CLI token to be configured first.
- **SkillNet**: An academic skill registry. Two install methods: `<name>@skillnet` (auto-searches to find URL then installs) and `/skills skillnet install <url>` (direct URL install).
- **Marketplace source**: A remote Git repository that hosts available skills. Each source has a name, URL, and enabled/disabled state.
- **Spec**: The install identifier format supporting: `<skill>@builtin` (builtin), `<slug>@clawhub` (ClawHub), `<skill>@<marketplace>` (Git marketplace); bare names without `@` are auto-detected as builtin if applicable.
- **Local install**: Use `/skills install <path>` to install from a local directory (must contain `SKILL.md`) or remote archive URL; paths/URLs are auto-detected and routed to the local import flow.
- **Install location**: The directory where a skill is stored after installation (`~/.jiuwenswarm/agent/jiuwenswarm_workspace/skills/`).
- **Source tag**: Each skill in the list is tagged with its source: `[builtin]` = builtin, `[local]` = imported, `[clawhub]` = ClawHub, `[skillnet]` = SkillNet, `[project]` or marketplace name = other.

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
- **Path/URL auto-detection**: When installing with `/skills install <path_or_url>` (local path like `/path/to/skill` or `C:\skill`, or remote URL `https://...`), the system automatically routes to the local import flow (`skills.import_local`). All URLs go through import_local; SkillNet is not auto-routed from URLs.
- **`@skillnet` search-install**: When using `/skills install <name>@skillnet`, the frontend first calls `skills.skillnet.search`. **Only auto-installs if an exact match by skill_name is found**; with no exact match, it only displays search results (with URLs and names) without auto-installing the first result — the user must choose one and install via `/skills skillnet install <url>` or `/skills install <exact_name>@skillnet`. This is because SkillNet search is semantic: searching "code" may return "taskflow", "coding-agent" etc. whose names don't contain "code".
- **ClawHub token required**: A ClawHub CLI token must be configured before installing from ClawHub (via `/skills marketplace clawhub token <value>`). Without a token, `@clawhub` installs will fail with a message explaining how to set the token. Obtain your token at [clawhub.ai](https://clawhub.ai).
- **ClawHub slug vs. display name**: ClawHub skills are identified by their unique **slug** (e.g., `code-review-security`), not their display name (e.g., "Code Review Assistant"). When a direct slug install fails, the system automatically searches ClawHub and displays matching results (with real slugs and summaries) to help you find the correct skill.
- **ClawHub overwrite confirmation**: When the target slug already exists (same name from any source counts as installed), TUI presents an interactive prompt: "Skill xxx is already installed. Do you want to force overwrite?". Choosing "Yes" re-installs with `force: true`, replacing the old skill; choosing "No" or exiting keeps the existing skill unchanged. The Web UI bypasses confirmation and uses `force: true` directly.
- **SkillNet async install**: SkillNet installation is asynchronous — it initiates a download task and returns an `install_id`, then TUI automatically polls `install_status` every 800ms until completion or failure (max wait: 15 minutes). Progress is shown as `Downloading... (install_id: xxx)`.
- **SkillNet overwrite confirmation**: Same as ClawHub — TUI prompts the user interactively when a skill already exists. Web UI uses `force: true` directly.
- **SkillNet accessible in China**: SkillNet search API is hosted at `http://api-skillnet.openkg.cn` (OpenKG platform) and is directly accessible in China without VPN. However, the skill content itself is hosted on GitHub, which may require VPN.
- **Same-name skills cannot coexist**: Skills are stored as directories at `skills/{name}/`, and the filesystem does not allow two directories with the same name. Installing a skill with the same name from a different source will overwrite the previous one (with user confirmation). `/skills use` only uses the skill name and cannot distinguish between sources.
- **ClawHub network access**: ClawHub API is hosted at `https://clawhub.ai`. VPN may be required in regions with restricted access to this domain.
- **Cache cleanup**: `marketplace remove` sends `{ name, remove_cache: true }` to also clear the local cache for that source.
- **Auto-refresh**: `marketplace add`, `marketplace remove`, and `marketplace toggle` automatically re-list marketplace sources after a successful operation.
- **Offline handling**: `/skills use` checks connection status; if offline, shows `offline: waiting for reconnect before sending /skills use request`.

#### Examples

- `/skills` — List skills (grouped: Installed / Available)
- `/skills list` — List skills (explicit subcommand)
- `/skills install advanced-daily-report` — Install a builtin skill (bare name auto-detect)
- `/skills install advanced-daily-report@builtin` — Install a builtin skill (explicit format)
- `/skills install code-review@clawhub` — Install a skill from ClawHub (using slug)
- `/skills install code-review@skillnet` — Install from SkillNet (auto-searches for URL)
- `/skills skillnet search code-review` — Search SkillNet skill registry
- `/skills skillnet install https://github.com/user/skill-repo` — Install via SkillNet subcommand (direct URL)
- `/skills install my-skill@marketplace` — Install a skill from Git marketplace
- `/skills install /path/to/my-skill` — Install a skill from local directory
- `/skills install https://example.com/skill.zip` — Install from remote URL (local import)
- `/skills uninstall my-skill` — Uninstall a skill
- `/skills marketplace list` — List marketplace sources
- `/skills marketplace add community https://github.com/user/skills-repo` — Add a marketplace source named "community"
- `/skills marketplace remove community` — Remove the "community" marketplace source
- `/skills marketplace toggle community on` — Enable the "community" marketplace source
- `/skills marketplace toggle community off` — Disable the "community" marketplace source
- `/skills marketplace clawhub` — View ClawHub token status
- `/skills marketplace clawhub token abc123xyz` — Set ClawHub CLI token
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
| `/sandbox` or `/sandbox status` | Show current runtime (`enabled`, `landlock`, `excluded_commands`, `files.allow_write`, `files.deny_write`) |
| `/sandbox enable` | Enter sandbox mode (spawns jiuwenbox if needed, rebuilds agent) |
| `/sandbox disable` | Leave sandbox mode (rebuilds agent; stops jiuwenbox only if jiuwenswarm started it) |
| `/sandbox exclude add <pattern>` | Add a shell glob whose matches run locally instead of in the sandbox |
| `/sandbox exclude remove <pattern>` | Remove a pattern |
| `/sandbox exclude list` | List current `excluded_commands` |
| `/sandbox files allow <path>` | Allow write access to `<path>` inside the sandbox (shown as rw) |
| `/sandbox files deny <path>` | Deny write access to `<path>` inside the sandbox (read still allowed, shown as ro) |
| `/sandbox files remove <path>` | Remove `<path>` from the user-configured allow & deny sets |
| `/sandbox files list` | List effective `allow_write` / `deny_write` |
| `/sandbox help` | Print usage |

#### Concepts

- **Platform support**: `/sandbox` is Linux-only (jiuwenbox depends on Linux kernel features such as bwrap, Landlock, and Linux namespaces). On a Windows or macOS agent-server, every `/sandbox` sub-command returns a `SANDBOX_BAD_REQUEST` error. If the TUI runs on Windows/macOS but the agent-server is on a Linux host, the command works — what matters is the agent-server's platform.
- **Write policy semantics**: `allow` / `deny` control **write access** (rw/ro) inside the sandbox, not Unix octal modes. Enforcement uses bwrap bind mounts + `--remount-ro`; Landlock is defense-in-depth (when `landlock.compatibility=disabled`, bwrap is primary).
- **Nested paths**: Supported: parent allow + child deny (e.g. allow `/tmp`, deny `/tmp/secret`). Not supported: child allow + parent deny (parent deny wins); the server rejects such configs.
- **Effective write policy**: `files.allow_write` / `files.deny_write` in the status panel show the merged view of auto-managed and user-configured entries, each labeled `(rw)` or `(ro)`. Auto-managed entries are server-injected (intrinsic files such as `AGENT.md`, `HEARTBEAT.md`, `IDENTITY.md`, `SOUL.md`, `USER.md`, the `memory/daily_memory/` directory, and depending on the mode, `project_dir` and `config/config.yaml`) and cannot be removed via `/sandbox files remove`.
- **preserve_file_sharing_mode**: Controlled by jiuwenswarm config, not by `/sandbox`. Only `mount` is supported: intrinsic files and `project_dir` are bind-mounted into the sandbox and `project_dir/config/config.yaml` is explicitly added to `deny_write`. Writing any other value into config.yaml is rejected by the server.
- **excluded_commands**: Match the full command string (not just `argv[0]`); a match makes that tool call run on the host, effectively granting the command's side effects to the local environment.
- **Add / remove are strict**: `exclude add` rejects a pattern that is already in the list; `exclude remove` rejects a pattern that is not in the list. `files allow|deny` rejects a path that is already in the same bucket, and rejects a path that exists in the opposite bucket (allow vs deny conflict) — run `files remove` first if you want to flip it. `files remove` rejects paths that have no matching user-configured entry.
- **enable / disable**: Triggers an agent rebuild. The response lists `rebuilt_modes` (typically `agent.*` / `code.*`) and the jiuwenbox endpoint.

#### Examples

- `/sandbox enable` — turn on sandbox mode
- `/sandbox status` — see runtime + effective files
- `/sandbox files allow ./tmp/` — allow sandbox write access to `./tmp/` (rw)
- `/sandbox files deny ./tmp/secret/` — deny write under an allowed parent (ro)
- `/sandbox exclude add "git *"` — let `git` run on the host instead of inside the sandbox

### `/keybindings` (Keyboard Shortcuts)

View, edit, or reset TUI keyboard shortcuts. Config file: `~/.jiuwenswarm-tui/keybindings.json`.

#### Usage

| Command | Action |
|---------|--------|
| `/keybindings` | Same as `/keybindings edit` |
| `/keybindings edit` | Create or open the config file; reload after the external editor closes |
| `/keybindings list` | List effective shortcuts grouped by context |
| `/keybindings reset` | Delete user config and restore built-in defaults |

Alias: `/keybind`.

#### Notes

- Built-in defaults are merged with user JSON per **context**; set a key to `null` to unbind a default.
- Key ids must match pi-tui `matchesKey` format (`ctrl`/`shift`/`alt` + main key); chords are not supported.
- **Non-rebindable**: `ctrl+c`, `ctrl+d`, `ctrl+m` (reserved keys).
- Select-list navigation, config editor text input, Resume preview/rename sub-states, etc. remain hardcoded.

See the Chinese [TUI User Guide · Keyboard shortcuts](../zh/TUI使用指南.md#快捷键) for the full context/action reference.

### `/hooks` (Browse Hooks Configuration)

View a summary of all hooks configured in `config.yaml` (read-only).

#### Usage

- `/hooks` (no parameters, no subcommands)

#### Data Source

The TUI requests the Gateway via the `hooks.list` RPC, which loads the `hooks` section from `config.yaml` and returns a summary.

#### Display Contents

`/hooks` displays hooks configuration in three levels:

1. **Event List (Level 1)**: All events sorted by hook count in descending order. Each row shows event name and hook count; the description column shows hook count distribution per matcher.
2. **Status Panel**:
   - `Source` — Configuration source (`config.yaml`)
   - `Global Status` — Global toggle state (`enabled` / `DISABLED`)
   - `Total Hooks` — Total hook count across all events
   - `Active Events` — Events with at least 1 hook / total events (out of 17)
3. **Hook Detail Cards (Level 2)**: Grouped by `Event > Matcher`, each hook shows:
   - `Type` — `command` (shell command) or `prompt` (LLM review)
   - `Command` / `Prompt` — The hook content
   - `Timeout` — Timeout in seconds
   - `Shell` — Execution shell (command hooks only)
   - `Status` — Status message

#### When No Hooks Are Configured

If `config.yaml` has no hooks configured, displays `No hooks configured.` with a hint to use `/config edit` to configure them.

#### Hooks Concept Overview

Hooks are extension logic that executes automatically when specific events fire. 17 events are supported:

| Event | Execution Layer | Trigger |
|---|---|---|
| `PreToolUse` | Agent Rail | Before a tool call |
| `PostToolUse` | Agent Rail | After a successful tool call |
| `PostToolUseFailure` | Agent Rail | After a failed tool call |
| `Stop` | Agent Rail | After agent response completes |
| `PermissionRequest` | Agent Rail | On permission request |
| `PermissionDenied` | Agent Rail | On permission denied |
| `SubagentStart` | Agent Rail | When a sub-agent starts |
| `SubagentStop` | Agent Rail | When a sub-agent stops |
| `BeforeModelCall` | Agent Rail | Before a model call |
| `AfterModelCall` | Agent Rail | After a model call |
| `UserPromptSubmit` | Gateway | User submits a message |
| `SessionStart` | Gateway | Session starts |
| `SessionEnd` | Gateway | Session ends |
| `Notification` | Gateway | Notification is sent |
| `ConfigChange` | Gateway | Configuration changes |
| `InstructionsLoaded` | Gateway | Instructions are loaded |
| `Setup` | Gateway | Initialization |

Two hook types are supported:

| Type | Description | Key Parameters |
|---|---|---|
| `command` | Executes a shell command (subprocess). Receives JSON context via `$ARGUMENTS` env var. Exit code 0 = success, 2 = block. | `command`, `timeout` (default 30s), `shell` (default bash) |
| `prompt` | Invokes LLM review. `$ARGUMENTS` in the template is replaced with JSON context, `$TOOL_NAME` with the tool name. LLM response JSON with `decision: "block"` blocks the operation. | `prompt`, `timeout` (default 15s), `model` |

- **Blocking**: Exit code 2 (command) or `decision: "block"` (prompt) blocks the current operation (e.g., skip tool call) and feeds the reason back to the model.
- **Input Modification**: PreToolUse hooks can modify tool input parameters via `modifiedInput` in stdout JSON.
- **Additional Context**: Extra information can be injected into tool results or model context via `additionalContext` in stdout JSON.
- **Global Toggle**: `hooks.disable_all_hooks: true` in `config.yaml` disables all hooks.

#### Configuration Example

```yaml
hooks:
  PreToolUse:
    - matcher: "write_file"
      hooks:
        - type: command
          command: "echo 'write_file about to execute' >> /tmp/hooks.log"
          timeout: 10
    - matcher: "bash|run_command"
      hooks:
        - type: prompt
          prompt: "Review if this command is safe: $ARGUMENTS"
          timeout: 20
  SessionStart:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo 'Session started: $ARGUMENTS' >> /tmp/hooks.log"
```

#### Example

- `/hooks` — Browse all current hooks configuration

### `/agents` (Agent Management)

Manage custom agents (subagents) throughout their full lifecycle: view, create, update, enable/disable, and delete. Agent definitions are stored as Markdown files and support four-level source priority merging.

- **Parsing location**: TUI local parsing, calling backend `agents.*` endpoints via RPC.
- **Applicable modes**: All.
- **Note**: This command is registered as hidden (`hidden: true`) and does not appear in `/help` listings but can be used directly.

#### Subcommands

| Command | Description |
|---|---|
| `/agents` or `/agents list` | List all agents (name, source, enabled status, description summary) |
| `/agents get <name>` | View full details of a specific agent (including System Prompt) |
| `/agents create [--project\|--local] <name> <description>` | Create a custom agent; LLM auto-generates the prompt |
| `/agents update <name> [--generate] <new description>` | Update agent description; add `--generate` to have LLM rewrite the prompt |
| `/agents enable <name>` | Enable a custom agent (cannot operate on builtin agents) |
| `/agents disable <name>` | Disable a custom agent (cannot operate on builtin agents) |
| `/agents delete <name>` | Delete a custom agent (cannot operate on builtin agents) |

#### Agent Sources & Storage

| Source | Storage Location | Priority | Manageable |
|--------|-----------------|----------|------------|
| `builtin` | In-code builtins | Lowest | Cannot enable/disable/delete |
| `local` | `<workspace>/.jiuwenswarm/agents-local/` | Local | Full lifecycle management |
| `user` | `~/.jiuwenswarm/agents/` | User | Full lifecycle management (default `create` location) |
| `project` | `<workspace>/.jiuwenswarm/agents/` | Highest | Full lifecycle management |

Agents with the same name are resolved by `project > user > local > builtin` priority; shadowed agents are marked with `shadowed_by`.

#### Agent Definition Fields

| Field | Description |
|------|------|
| `name` | Agent name (unique identifier) |
| `description` | Brief description |
| `prompt` | System Prompt text |
| `source` | Origin (`builtin` / `user` / `project` / `local`) |
| `file_path` | Agent definition file path |
| `model` | Specified model (`null` = use default) |
| `tools` | Available tool list |
| `disallowed_tools` | Disallowed tool list |
| `color` | Display color |
| `permission_mode` | Permission mode |
| `memory_scope` | Memory scope |
| `when_to_use` | When-to-use description |
| `max_iterations` | Max iterations (default 200) |
| `skills` | Associated skill list |
| `enabled` | Enabled status (`true` / `false` / `null`) |
| `shadowed_by` | Which source shadows this agent (`null` = active) |

#### `/agents create` Behavior

- **Argument parsing**: `--project` / `--local` are positional flags and must precede the name (e.g., `/agents create --project my-agent description`).
- **LLM generation**: By default, the current model auto-generates `when_to_use` and `system_prompt`; falls back to a built-in template on failure.
- **Auto-enable**: After creation, automatically writes `react.subagents.<name>.enabled = true` to `config.yaml` and hot-reloads the configuration.
- **Timeout**: 60 seconds.
- **Output**: Displays LLM generation marker, storage location, and file path.

#### `/agents update` Behavior

- **No description**: When no description is provided, shows current agent details (same as `get`) and prints usage.
- **`--generate`**: Explicitly triggers LLM prompt rewriting; without this flag, the request's template values are used.
- **Auto hot-reload**: Configuration is automatically reloaded after update.

#### `/agents enable` / `disable` Constraints

- Builtin agents (`source == "builtin"`) cannot be enabled/disabled; the backend returns an error.
- The operation writes to `config.yaml`'s `react.subagents.<name>.enabled` and hot-reloads.

#### `/agents delete` Constraints

- Builtin agents cannot be deleted.
- After deletion, the entry is automatically removed from `config.yaml`'s `react.subagents` and hot-reloaded.

#### `/agents get` Display

Displays all agent definition fields as key-value pairs, with the full System Prompt text appended at the end.

#### Tab Completion

The `get`, `update`, `enable`, `disable`, and `delete` subcommands support Tab completion on agent names (fetched via the `agents.list` RPC).

#### Examples

```bash
/agents                            # List all agents
/agents list                       # Same as above
/agents get Explore                # View Explore agent details
/agents create bug-hunter Root cause analysis expert     # Create user-level agent
/agents create --project proj-agent Project-level        # Create project-level agent
/agents create --local local-agent Local use only        # Create local agent
/agents update bug-hunter --generate Better description  # Update with LLM prompt rewrite
/agents enable bug-hunter           # Enable agent
/agents disable bug-hunter          # Disable agent
/agents delete my-agent             # Delete agent
```

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
| `/statusline help` | Show usage guide (writing patterns, practical examples, field list) |
| `/statusline json` | Show the actual current JSON data values (useful for debugging jq expressions) |

#### Concepts

- **StatusLine**: A text area at the bottom of the TUI that displays user-defined dynamic information, supporting multi-line output. When a custom statusline is configured, the built-in status line is automatically hidden to avoid redundant information.
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
| `mode` | Current mode (`agent.plan` / `agent.fast` / `code.normal` / `code.team` / `team`) |
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
| `trusted_dirs` | Trusted workspace directories (array of path strings) |
| `usage.total_input_tokens` | Total input tokens for session |
| `usage.total_output_tokens` | Total output tokens for session |
| `usage.total_tokens` | Total tokens for session |
| `context_window.context_window_size` | Model max context window tokens (e.g. 200000) |
| `context_window.used_percentage` | Context occupancy percentage (0-100) |
| `context_window.remaining_percentage` | Context remaining percentage (0-100) |

#### Command Writing Template

Use the following template to write commands. `input=$(cat)` reads JSON into a variable, then `echo "$input" | jq -r .field` extracts each field. `// "default"` is jq's fallback syntax — when a field is null or empty, the default value is used.

**General formula**:

```
/statusline set 'input=$(cat); field1=$(echo "$input" | jq -r '.field1 // "default"'); field2=$(echo "$input" | jq -r '.field2 // "default"'); echo "format string"'
```

**Recommended universal command** (shows mode, model, tokens, context %, connection):

```
/statusline set 'input=$(cat); mode=$(echo "$input" | jq -r '.mode // "?"'); model=$(echo "$input" | jq -r '.model // "?"'); tokens=$(echo "$input" | jq -r '.usage.total_tokens // 0'); pct=$(echo "$input" | jq -r '.context_window.used_percentage // 0'); conn=$(echo "$input" | jq -r '.connection // "?"'); echo "$mode | $model | ctx:${pct}% | tokens:$tokens | $conn"'
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
| Trusted dirs | `jq -r '(.trusted_dirs // []) | join(" ")'` |
| Total input tokens | `jq -r '.usage.total_input_tokens // 0'` |
| Total output tokens | `jq -r '.usage.total_output_tokens // 0'` |
| Total tokens | `jq -r '.usage.total_tokens // 0'` |
| Context window size | `jq -r '.context_window.context_window_size // 0'` |
| Context used % | `jq -r '.context_window.used_percentage // 0'` |
| Context remaining % | `jq -r '.context_window.remaining_percentage // 0'` |

#### More Examples

- `/statusline` — View current configuration
- `/statusline set 'input=$(cat); model=$(echo "$input" | jq -r .model); echo "$model"'` — Show model name only
- `/statusline set 'input=$(cat); proc=$(echo "$input" | jq -r .is_processing); model=$(echo "$input" | jq -r .model); echo "$proc | $model"'` — Show processing state and model
- `/statusline set 'input=$(cat); pct=$(echo "$input" | jq -r .context_window.used_percentage); rem=$(echo "$input" | jq -r .context_window.remaining_percentage); cw=$(echo "$input" | jq -r ".context_window.context_window_size / 1000"); echo "ctx:${pct}% used (${rem}% left, ${cw}K window)"'` — Show context window occupancy with percentage bar
- `/statusline set 'input=$(cat); pct=$(echo "$input" | jq -r ".context_window.used_percentage // 0"); if [ "$pct" -ge 90 ]; then warn="⚠HIGH"; elif [ "$pct" -ge 70 ]; then warn="~MED"; else warn="OK"; fi; echo "ctx:${pct}% $warn"'` — Show context % with threshold warning (≥90% HIGH, ≥70% MED)
- `/statusline set 'input=$(cat); err=$(echo "$input" | jq -r .last_error); if [ "$err" != "null" ] && [ "$err" != "" ]; then echo "error: $err"; else echo "ok"; fi'` — Show error when present, otherwise "ok"
- `/statusline set 'input=$(cat); dirs=$(echo "$input" | jq -r '.trusted_dirs // [] | join(" ")'); mode=$(echo "$input" | jq -r '.mode // "?"'); echo "$mode | dirs:$dirs"'` — Show mode and trusted workspace directories
- `/statusline clear` — Remove status line configuration
- `/statusline help` — View usage guide (writing patterns, practical examples, available fields)
- `/statusline json` — View actual current JSON data values (useful for debugging jq expressions)

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
    "command": "input=$(cat); mode=$(echo \"$input\" | jq -r '.mode // \"?\"'); model=$(echo \"$input\" | jq -r '.model // \"?\"'); pct=$(echo \"$input\" | jq -r '.context_window.used_percentage // 0'); tokens=$(echo \"$input\" | jq -r '.usage.total_tokens // 0'); echo \"$mode | $model | ctx:${pct}% | tokens:$tokens\"",
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
| `/auto-harness issue fix <issue_numbers>` | Create fix tasks for GitCode issues |
| `/auto-harness issue scan [--repo <repo>] [--page <n>] [--labels <labels>] [--force-refresh]` | Scan repo GitCode issues |
| `/auto-harness issue status` | View GitCode issue processing status |
| `/auto-harness issue delete <issue_numbers>` | Delete issue processing records |

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

### `/auto-harness issue` (GitCode Issue Auto-Fix)

Manage GitCode issue auto-processing: scan issue matrix, create fix tasks, view status, clean up records.

Requires `git.user_name`, `git.user_email` and `gitcode.access_token` (or `GITCODE_ACCESS_TOKEN` env var) to be configured.

#### Subcommands

| Command | Description |
|---|---|
| `/auto-harness issue fix <issue_numbers>` | Create fix tasks for GitCode issues |
| `/auto-harness issue scan [--repo <repo>] [options]` | Scan repo issues |
| `/auto-harness issue status` | View issue processing status |
| `/auto-harness issue delete <issue_numbers>` | Delete issue processing records |

#### `/auto-harness issue fix` (Create Fix Task)

- Usage: `/auto-harness issue fix <issue_numbers>`
- Parameters:
  - `<issue_numbers>`: Issue number(s), comma-separated, e.g. `1272,1271,1270`
  - `--repo <repo>`: Target repository (`jiuwenswarm` / `agent_core`); interactively selected if not specified
- Issues with bound PRs (open or merged) are automatically skipped
- Examples:
  - `/auto-harness issue fix 1286`
  - `/auto-harness issue fix 1272,1271,1270`

#### `/auto-harness issue scan` (Scan Issue)

- Usage: `/auto-harness issue scan`
- Parameters:
  - `--repo <repo>`: Target repository; interactively selected if not specified
  - `--page <n>`: Page number, default 1
  - `--labels <labels>`: Label filter, comma-separated; defaults to bug type only
  - `--force-refresh`: Force refresh from GitCode API (uses cache by default)
- Displays: issue number, title, labels, difficulty, last updated
- Examples:
  - `/auto-harness issue scan`
  - `/auto-harness issue scan --repo jiuwenswarm --page 1`
  - `/auto-harness issue scan --repo agent_core --force-refresh`

#### `/auto-harness issue status` (View Status)

- Usage: `/auto-harness issue status` (no parameters)
- Lists all issue processing records in table format: number, status, stage, progress, details
- Example: `/auto-harness issue status`

#### `/auto-harness issue delete` (Delete Records)

- Usage: `/auto-harness issue delete <issue_numbers>`
- Parameters:
  - `<issue_numbers>`: Issue number(s) to delete
- Examples:
  - `/auto-harness issue delete 123`
  - `/auto-harness issue delete 123 456`

---

## Planned Features

| Command | Description |
|---|---|
| `/btw` | Ask question |
| `/memory` | Memory management |
| `/export` | Export related files |
| `/permissions` | Permission management |
