## CLI / channel control commands

JiuwenSwarm supports **special prefix commands** to control sessions and modes. Common ones:

- `/new_session`: start a new `session_id` for the current channel
- `/mode plan`, `/mode fast`, `/mode team`, `/mode code`: switch the channel's working mode

These are handled in the Gateway **`MessageHandler`** and **are not** sent to the agent.

---

### 1. `/new_session` — new session id

**Behavior**

- For supported channels (`feishu` / `xiaoyi` / `dingtalk` / `whatsapp` / `wecom` / `wechat`), generates a new `session_id`, e.g.:  
  - `feishu_<ms hex>_<random hex>`
  - `xiaoyi_<ms hex>_<random hex>`
  - `dingtalk_<ms hex>_<random hex>`
  - `whatsapp_<ms hex>_<random hex>`
  - `wecom_<ms hex>_<random hex>`
  - `wechat_<ms hex>_<random hex>`
- Later messages on that channel use this id, so a new folder appears under `workspace/session/`.

**Usage**

Send in a supported channel:

  ```text
  /new_session
  ```
![](../assets/images/命令行解析.jpg)

The Gateway will:

  1. Intercept (not forwarded to the agent)
  2. Generate a new `session_id` for that `channel_id`
  3. Reply with a system message, e.g.  
     `session_id updated to feishu_17f2b4b32e0_ab12cd`

**Notes**

- `/new_session` only changes **future** message binding; the directory is created when the session is actually used (todo, files, etc.).

---

### 2. `/mode` — channel mode (`plan` / `fast` / `code` / `team`)

**Behavior**

- Sets a logical **mode** for the channel:
  - `plan`: planning, explanation, decomposition (default)
  - `fast`: more hands-on execution
  - `code`: code generation and execution mode (defaults to `code.normal`; use `/mode code.plan` for code planning)
  - `team`: team mode
- You can also specify sub-modes directly:
  - `agent.plan` or `plan` → Agent Plan mode
  - `agent.fast` or `fast` → Agent Fast mode
  - `code.plan` → Code Plan mode
  - `code.normal` or `code` → Code Normal mode
  - `team` → Team mode
- Mode is passed in `params["mode"]` for prompt construction.

**Usage**

  ```text
  /mode plan
  ```

  or

  ```text
  /mode fast
  ```

The Gateway will:

  1. Treat as control, not forward to the agent
  2. Update `ChannelControlState.mode`
  3. Reply e.g. `mode updated to fast`

**Scope**

- Stored **per channel** (`channel_id` → `mode`). All later messages on that channel use the current mode.
- Initial value can come from `default_mode` in config; `MessageHandler` reads it on startup.

---

### 3. TUI: `/workspace_dir` — workspace path for outbound requests

**Scope:** terminal UI (`jiuwenclaw-cli`) only; parsed locally, not by the Gateway control pipeline.

**Behavior**

- **`/workspace_dir`** or **`/workspace_dir get`**: show the saved workspace directory (if any).
- **`/workspace_dir set <path>`**: save a path (spaces allowed). Example: `/workspace_dir set C:\Projects\my-app`
- **`/workspace_dir clear`**: clear the saved value.
- Alias: **`/workspace-dir`**.

**Persistence**

- Stored as a single-line file: **`~/.jiuwenclaw/tui-workspace-dir`**.

**Gateway / Agent**

- When a non-empty path is set, TUI includes **`workspace_dir`** in the WebSocket **`params`** for fire-and-forget requests built by `sendEventOnly` (e.g. `chat.send`), so Gateway and AgentServer can read it from `Message.params` / `AgentRequest.params`. Downstream usage depends on the agent and extensions.

---

### 4. `/compact` — context compression

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

