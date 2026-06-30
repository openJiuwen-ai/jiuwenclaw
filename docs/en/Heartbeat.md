# Heartbeat

Heartbeat is jiuwenSwarm's periodic liveness probe and task runner. The gateway sends requests to AgentServer at a fixed interval to verify connectivity and agent availability. If **`workspace/HEARTBEAT.md`** is configured, the agent can also run predefined tasks on each beat. Results can be relayed to a chosen channel (default: web).

---

## 1. Overview

- **Liveness**: Sends requests to AgentServer on an interval to confirm the service is healthy.
- **Optional tasks**: If `workspace/HEARTBEAT.md` exists at the project root, the agent reads and executes the active task items in order and returns results. Without this file or empty tasks, only `HEARTBEAT_OK` is returned.
- **Result relay**: Heartbeat responses can be forwarded to a configured channel (e.g., web UI) for visibility of the latest heartbeat status and content.

---

## 2. Configuration

Three configuration methods are available: config file, environment variables, or web UI.

### 2.1 Config file `config/config.yaml`

Configure the `heartbeat` section in `config/config.yaml`:

```yaml
heartbeat:
  # Interval in seconds; default 3600
  every: 3600
  # Channel for relaying results (e.g., "web" = web UI)
  target: web
  # Active window in local time; heartbeat only within this range; omit for 24/7
  active_hours:
    start: 08:00
    end: 22:00
```

| Field | Meaning | Notes |
|-------|---------|-------|
| `every` | Interval (seconds) | Must be > 0; e.g., 60 = every minute, 3600 = hourly |
| `target` | Relay channel | Usually `web`, which pushes heartbeat responses to the web UI; empty or omitted = no relay |
| `active_hours` | Active window | `start`/`end` in `HH:MM` (24-hour format). Heartbeat only fires within `[start, end]`. Supports cross-midnight windows (e.g., 22:00–06:00). |

### 2.2 Environment variables (override YAML)

| Variable | Meaning | Example |
|----------|---------|---------|
| `HEARTBEAT_INTERVAL` | Interval (seconds) | `3600` |
| `HEARTBEAT_RELAY_CHANNEL_ID` | Relay channel | `web` |
| `HEARTBEAT_TIMEOUT` | Single heartbeat timeout (seconds) | `30` |

Environment variables take precedence over the `heartbeat` section in `config/config.yaml`.

### 2.3 Web UI Heartbeat panel

Open **Heartbeat** in the left sidebar:

- View current heartbeat configuration (interval, relay target, active window)
![](../assets/images/heartbeat1.png)
- Edit and save configuration (writes `config/config.yaml` and restarts the heartbeat service)
![](../assets/images/heartbeat2.png)
- View the last 20 heartbeat records, including status (normal / warning), content, and timestamps
![](../assets/images/heartbeat3.png)

---

## 3. HEARTBEAT.md and periodic tasks

### 3.1 File location and role

- **Path**: In the web UI, this file appears on the right side of the Heartbeat panel. Click `Edit` to modify it.
![](../assets/images/heartbeat5.png)
- **Role**: If the file exists and contains tasks, each heartbeat reads this content, executes tasks in order, and returns results. Without this file or empty tasks, only `HEARTBEAT_OK` is returned.

### 3.2 Agent behavior

The server reads `HEARTBEAT.md`, parses the task list, and sends a chat request to the agent following the normal conversation flow. If parsing fails or the task list is empty, `HEARTBEAT_OK` is returned directly. Otherwise, tasks are executed and responses returned.

---

## 4. Web UI display and events

- **Status**: The Heartbeat panel and toolbar display the latest heartbeat info (e.g., `HEARTBEAT_OK`) and timestamp.
- **Events**: When `target` is `web`, each heartbeat response is pushed to the frontend via the `heartbeat.relay` event for status and history updates. If content is not `HEARTBEAT_OK`, a popup notification appears for viewing task results or errors.
![](../assets/images/heartbeat4.png)

---

## 5. FAQ

**Q: I changed the heartbeat section in `config/config.yaml` but nothing happened.**  
A: Config is read at startup. If you use the web UI Heartbeat panel, it rewrites YAML and automatically restarts the heartbeat service. If you edit YAML directly, restart the entire application (e.g., `jiuwenswarm-web`) for changes to take effect.

**Q: How do I send heartbeats only during work hours?**  
A: Set `heartbeat.active_hours.start` / `end`, e.g., `start: 09:00`, `end: 18:00`. Heartbeats only fire within this window.

**Q: What if a heartbeat request times out?**  
A: Set the `HEARTBEAT_TIMEOUT` environment variable (seconds). On timeout, the beat is marked failed and a WARNING is logged.

**Q: Where must `HEARTBEAT.md` live?**  
A: At the DeepAgent workspace root: `~/.jiuwenswarm/agent/workspace/HEARTBEAT.md`. Otherwise, it is treated as "no custom tasks" and only `HEARTBEAT_OK` is returned.

---

## 6. Code and config index

- Service: `jiuwenswarm/gateway/heartbeat/heartbeat.py`.
- Config reading and writing: `jiuwenswarm/common/config.py` (`update_heartbeat_in_config`); `app.py` builds `HeartbeatConfig` from the `heartbeat` section in `~/.jiuwenswarm/config/config.yaml` and environment variables at startup.
- Agent-side HEARTBEAT.md handling: `jiuwenswarm/server/runtime/agent_adapter/interface_deep.py` detects heartbeat sessions and reads `HEARTBEAT.md` to trigger tasks.
- Frontend: `jiuwenswarm/channels/web/frontend/src/components/HeartbeatPanel/`, `heartbeat.get_conf` / `heartbeat.set_conf`, `heartbeat.relay` events.

*Document version: v1.0*  
*Audience: jiuwenSwarm users*  
*Last updated: 2026-06-25*  
*Simplified Chinese: [心跳](../zh/心跳.md)*