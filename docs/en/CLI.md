## CLI / Channel Control Commands

JiuwenSwarm supports **special prefix commands** to control sessions and modes. These commands are parsed by the Gateway's `MessageHandler` and **are not sent to the Agent**.

---

### Supported Channels

The following IM channels support control commands:

- `feishu`
- `xiaoyi`
- `dingtalk`
- `whatsapp`
- `wechat`

---

### 1. `/new_session` — New Session ID

**Behavior**

- Generates a new `session_id` for the current channel, formatted as `{channel_type}_{ms_timestamp_hex}_{random_hex}`
- All subsequent chat messages from this channel will be forced to use the new `session_id`

**Usage**

Send in a supported channel:

```text
/new_session
```

![](../assets/images/命令行解析.png)

The Gateway will:
1. Intercept this message (not forwarded to the Agent)
2. Cancel any tasks currently running in the session
3. Generate a new `session_id` for that `channel_id`
4. Reply with a system message, e.g.: `[Received CLI command], session_id changed to feishu_17f2b4b32e0_ab12cd`

---

### 2. `/mode` — Switch Channel Mode

**Behavior**

Sets the working mode for the current channel. The Agent uses this when constructing prompts and behavior strategies.

**Primary Modes (mapped to secondary modes):**

| Command | Maps to | Description |
|---------|---------|-------------|
| `/mode agent` | `agent.plan` | Agent mode, planning/explanation/decomposition |
| `/mode code` | `code.normal` | Code mode, Agent interacts via code execution tools |
| `/mode team` | `team` | Team mode |

**Direct Secondary Modes:**

| Command | Description |
|---------|-------------|
| `/mode agent.plan` | Agent mode + planning style (default) |
| `/mode agent.fast` | Agent mode + auto-execution style |
| `/mode code.plan` | Code mode + planning style |
| `/mode code.normal` | Code mode + direct execution style (default) |
| `/mode code.team` | Code mode + team style |

**Usage**

```text
/mode agent
```

or

```text
/mode code.plan
```

The Gateway will:
1. Intercept this message
2. Cancel tasks in the current session (if mode changes)
3. Update `ChannelControlState.mode`
4. Reply with a system message, e.g.: `[Received CLI command], mode changed to code.plan`

---

### 3. `/switch` — Switch Secondary Mode

**Behavior**

Switches secondary style within the current primary mode, more concise than `/mode`.

| Command | When in agent mode | When in code mode |
|---------|--------------------|--------------------|
| `/switch plan` | → `agent.plan` | → `code.plan` |
| `/switch fast` | → `agent.fast` | Not supported |
| `/switch normal` | Not supported | → `code.normal` |
| `/switch team` | Not supported | → `code.team` |

**Usage**

```text
/switch plan
```

---

### 4. `/skills list` — List Available Skills

**Behavior**

Queries the currently available skill list.

**Usage**

```text
/skills list
```

The Gateway will call `skills.list` and reply with the skill list as a notification.

---

### 5. `/branch` — Fork Session

**Behavior**

Forks a new session from the current one, preserving the original conversation history. Useful for exploring new directions without affecting the original session.

**Usage**

```text
/branch
```

Or with a custom name:

```text
/branch fix login issue
```

The Gateway will:
1. Call `session.fork` to create a new session
2. Switch to the new session
3. Reply with a message, e.g.: `[Received /branch command] Session "fix login issue" forked, now switched to new session.`

---

### 6. `/rewind` — Rewind Conversation

**Behavior**

Rewinds the current session to a specified turn, deleting that turn and all subsequent conversation records.

**Usage**

First send the rewind request:

```text
/rewind 3
```

The Gateway will reply with a confirmation prompt:

```
[Received /rewind 3 command] Confirm rewind to turn 3?
This operation is irreversible and will delete turn 3 and all subsequent conversations.
Please reply /rewind confirm 3 to confirm, or /rewind cancel to cancel.
Note: Rewind does not affect manually edited files or commands executed via bash.
```

Confirm execution:

```text
/rewind confirm 3
```

Cancel operation:

```text
/rewind cancel
```

---

### 7. TUI: `/workspace_dir` — Workspace Path for Outbound Requests

**Scope:** Terminal UI (`jiuwenswarm-tui`) only; parsed locally, not by the Gateway control pipeline.

**Behavior**

- **`/workspace_dir`** or **`/workspace_dir get`**: show the saved workspace directory (if any).
- **`/workspace_dir set <path>`**: save a path (spaces allowed). Example: `/workspace_dir set C:\Projects\my-app`
- **`/workspace_dir clear`**: clear the saved value.
- Alias: **`/workspace-dir`**.

**Persistence**

- Stored as a single-line file: **`~/.jiuwenswarm/tui-workspace-dir`**.

**Gateway / Agent**

- When a non-empty path is set, TUI includes **`workspace_dir`** in the WebSocket **`params`** for fire-and-forget requests built by `sendEventOnly` (e.g. `chat.send`), so Gateway and AgentServer can read it from `Message.params` / `AgentRequest.params`. Downstream usage depends on the agent and extensions.

---

### 8. `/compact` — Context Compression

**Scope:** TUI only; triggers context compression via AgentServer.

**Behavior**

- Actively triggers context compression to clean up conversation history while keeping summary information in context.
- TUI sends `command.compact` request to AgentServer.

**Usage**

```text
/compact
```

**Return Values**

- `busy`: Compression is already in progress, please try again later.
- `compressed`: Compression successful, displays token count before/after compression and savings percentage.
- `noop`: No compression needed, context is already optimized.

---

### Configuration Notes

- Mode is stored **per channel** (`channel_id` → `mode`). All subsequent messages on that channel will automatically include the current mode.
- `default_mode` can be set in `config.yaml` as the initial value; `MessageHandler` reads it on startup.
- `/new_session` and `/mode` changes will automatically cancel tasks currently running in the session.