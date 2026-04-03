## CLI / channel control commands

JiuwenClaw supports **special prefix commands** to control sessions and modes. Common ones:

- `/new_session`: start a new `session_id` for the current channel
- `/mode plan` or `/mode agent`: switch the channel’s working mode

These are handled in the Gateway **`MessageHandler`** and **are not** sent to the agent.

---

### 1. `/new_session` — new session id

**Behavior**

- For supported channels (`feishu` / `xiaoyi` / `dingtalk`), generates a new `session_id`, e.g.:  
  - `feishu_<ms hex>_<random hex>`
  - `xiaoyi_<ms hex>_<random hex>`
  - `dingtalk_<ms hex>_<random hex>`
- Later messages on that channel use this id, so a new folder appears under `workspace/session/`.

**Usage**

Send in the channel (Feishu / Xiaoyi / DingTalk):

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

### 2. `/mode` — channel mode (`plan` / `agent`)

**Behavior**

- Sets a logical **mode** for the channel:
  - `plan`: planning, explanation, decomposition (default)
  - `agent`: more hands-on execution
- Mode is passed in `params["mode"]` for prompt construction.

**Usage**

  ```text
  /mode plan
  ```

  or

  ```text
  /mode agent
  ```

The Gateway will:

  1. Treat as control, not forward to the agent
  2. Update `ChannelControlState.mode`
  3. Reply e.g. `mode updated to agent`

**Scope**

- Stored **per channel** (`channel_id` → `mode`). All later messages on that channel use the current mode.
- Initial value can come from `default_mode` in config; `MessageHandler` reads it on startup.

---

### 3. `/view` and `/ls` — file viewer & directory listing (CLI file commands)

**Purpose**

- View a file inside the Agent workspace (`/view`, alias `/cat`)
- List a directory (`/ls`)

Unlike `/new_session` and `/mode` (Gateway-only controls), these commands are **parsed by the Gateway and forwarded to AgentServer for execution**.

---

#### 3.1 Syntax

**View a file**

```text
/view <path>
/cat <path>
```

Optional line range parameters:

```text
/view <path> -n <lines>
/view <path> -f <from_line> -l <lines>
```

- `-f`: starting line number (1-based), default `1`
- `-l`: number of lines to display
- `-n`: number of lines to display (alias of `-l`)
- Precedence: if both `-l` and `-n` are present, `-l` wins (parser uses `lines = -l or -n`)

**List a directory**

```text
/ls
/ls <path>
```

- If `<path>` is omitted, it defaults to `.`.

---

#### 3.2 End-to-end flow (message → result)

**1) Gateway: parse + forward**

- Parser: `jiuwenclaw/gateway/message_handler.py::_parse_cli_file_command()`
  - Recognizes `/view`, `/cat`, `/ls` and extracts parameters
- Forwarder: `jiuwenclaw/gateway/message_handler.py::_forward_cli_file_command()`
  - `/view` → `ReqMethod.CLI_FILE_VIEW` (`cli.file.view`)
  - `/ls` → `ReqMethod.CLI_FILE_LIST` (`cli.file.list`)
  - Passes via `E2AEnvelope.params`:
    - `"path"`: the raw path string
    - `"params"`: line params for `/view` (`from_line`, `lines`); empty dict for `/ls`

**2) AgentServer: route to file service**

- Entry: `jiuwenclaw/agentserver/interface.py::_handle_cli_file_command()`
  - Calls:
    - `CLIFileService.handle_view_command(path, cmd_params)`
    - `CLIFileService.handle_ls_command(path)`

**3) File service: resolve + validate + read + format**

- Implementation: `jiuwenclaw/agentserver/cli_file_service.py`
- Key steps:
  - `resolve_path(path)` → absolute path
  - `is_path_allowed(full_path)` → security boundary check (agent_root only)
  - Existence / type checks
  - Size limit: max 1MB (`MAX_FILE_SIZE`)
  - Extension allowlist: `ALLOWED_EXTENSIONS`
  - Read and format output (default max 500 lines, `MAX_DISPLAY_LINES`)

**4) Gateway: send back to channel**

- Gateway sends `payload["content"]` back to the channel; if empty it falls back to `payload["error"]`.

---

#### 3.3 Path semantics & security boundary

**Path semantics**

- Current convention: **all relative paths are resolved against `agent_root`**
- Windows paths are normalized (`\` → `/`) before resolution
- Absolute paths are accepted by the resolver, but will still be blocked by the security boundary below

**Security boundary (hard constraint)**

- `CLIFileService.is_path_allowed()` only allows paths under `agent_root`
- Any path outside that tree (even if absolute) is rejected

---

#### 3.4 Output format

**`/view`**

- Returns a Markdown text containing:
  - a fenced code block with line numbers
  - a short summary (file path, total lines, displayed range)

**`/ls`**

- Returns a Markdown text containing:
  - directory header
  - sub-directories and files (with formatted sizes)
  - totals (dir/file counts)

---

#### 3.5 Common error cases

- Path resolution failed: `Path resolution failed: ...`
- Out-of-scope access: `Path is outside the allowed access scope`
- Not found: `File not found: ...` / `Directory not found: ...`
- Type mismatch: `Not a file: ...` / `Not a directory: ...`
- File too large: over 1MB
- Unsupported extension: not in allowlist

