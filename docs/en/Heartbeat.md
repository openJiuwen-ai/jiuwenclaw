# Heartbeat

**Heartbeat** is a periodic probe from the gateway to AgentServer to verify connectivity and agent health. If **`workspace/HEARTBEAT.md`** (under the agent workspace) is configured, the agent can also run listed tasks on each beat. You can choose which channel receives results (default: web).

---

## 1. Overview

- **Liveness**: Sends on a fixed interval to confirm the service is up.
- **Optional tasks**: If `workspace/HEARTBEAT.md` exists under the project root, the agent runs “active task items” in order and returns the results; otherwise the response is `HEARTBEAT_OK`.
- **Relay**: Heartbeat responses can be forwarded to a configured channel (e.g. web) for UI display.

---

## 2. Configuration

Three ways: config file, environment variables, or the web UI.

### 2.1 `config/config.yaml`

```yaml
heartbeat:
  # Interval in seconds, default 3600
  every: 3600
  # Relay target channel (e.g. "web" for the web UI)
  target: web
  # Active window in local time; omit for 24/7
  active_hours:
    start: 08:00
    end: 22:00
```


| Field | Meaning | Notes |
| ----- | ------- | ----- |
| `every` | Interval (seconds) | Must be &gt; 0. `60` = every minute, `3600` = hourly. |
| `target` | Relay channel | Often `web` to push to the web client; empty = no relay. |
| `active_hours` | Time window | `start` / `end` as `HH:MM` (24h). Heartbeat only fires when local time is in `[start, end]`. Omit for always on. Supports windows past midnight (e.g. 22:00–06:00). |


### 2.2 Environment variables (override YAML)


| Variable | Meaning | Example |
| -------- | ------- | ------- |
| `HEARTBEAT_INTERVAL` | Interval (seconds) | `3600` |
| `HEARTBEAT_RELAY_CHANNEL_ID` | Relay channel | `web` |
| `HEARTBEAT_TIMEOUT` | Single heartbeat timeout (seconds) | `30` |


Env vars override the `heartbeat` section in `config/config.yaml`.

### 2.3 Web UI — Heartbeat panel

Open **Heartbeat** in the sidebar to:

- View the current heartbeat configuration (interval, relay target, and active window)
![](../assets/images/heartbeat1.png)
- Edit and save (writes `config.yaml` and restarts the heartbeat service)  
![](../assets/images/heartbeat2.png)
- See the last 20 heartbeat messages (status, body, time)  
![](../assets/images/heartbeat3.png)

---

## 3. `HEARTBEAT.md` and periodic tasks

### 3.1 Location and role

- **Path**: In the web UI, the file appears on the right side of the panel and can be changed by clicking **Edit**.
![](../assets/images/heartbeat5.png)
- **Role**: If the file exists and lists tasks, each heartbeat run parses and executes them and returns the results; otherwise only `HEARTBEAT_OK` is returned.

### 3.2 Agent behavior

- The server reads `HEARTBEAT.md`, parses the task list, builds a chat request, and runs the normal flow. If parsing fails or the task list is empty, it returns `HEARTBEAT_OK`; otherwise, it executes the tasks and returns the response.

---

## 4. Web UI and events

- **Status**: The heartbeat panel and toolbar show the latest heartbeat information (such as `HEARTBEAT_OK`) and time.
- **Events**: When `target` is `web`, each heartbeat response is pushed to the frontend through the `heartbeat.relay` event to update status and history. Content other than `HEARTBEAT_OK` is displayed in a popup so task results or errors can be viewed.
![](../assets/images/heartbeat4.png)

---

## 5. FAQ

**Q: I edited `heartbeat` in `config.yaml` but nothing changed.**  
A: Config is read at startup. If you use the web panel, it rewrites YAML and restarts the heartbeat service. If you edit YAML by hand, restart the app (e.g. `jiuwenswarm-web`).

**Q: Heartbeats only during work hours?**  
A: Set `heartbeat.active_hours.start` / `end`, e.g. `09:00`–`18:00`.

**Q: Heartbeat timeout?**  
A: Set `HEARTBEAT_TIMEOUT` (seconds). On timeout the beat is marked failed and a WARNING is logged.

**Q: Where must `HEARTBEAT.md` live?**
A: At the DeepAgent workspace root: `~/.jiuwenswarm/agent/workspace/HEARTBEAT.md`. Otherwise it is treated as no custom tasks and only `HEARTBEAT_OK` is returned.

---

## 6. Code index

- Service: `jiuwenswarm/gateway/heartbeat/heartbeat.py`.
- Config: `jiuwenswarm/common/config.py` (`update_heartbeat_in_config`); at startup, `app.py` builds `HeartbeatConfig` from the `heartbeat` section of `~/.jiuwenswarm/config/config.yaml` and environment variables.
- Agent: `jiuwenswarm/server/runtime/agent_adapter/interface_deep.py` determines whether the current session is a heartbeat session, then reads `HEARTBEAT.md` and triggers the tasks.
- Web: `jiuwenswarm/channels/web/frontend/src/components/HeartbeatPanel/`, `heartbeat.get_conf` / `heartbeat.set_conf`, `heartbeat.relay`.
