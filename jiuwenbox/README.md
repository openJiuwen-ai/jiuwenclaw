# jiuwenbox

`jiuwenbox` is a lightweight Linux sandbox service for running agent tools and
code snippets with layered isolation.

It exposes a FastAPI server for sandbox lifecycle management, file transfer,
file listing/search, and command execution. Each sandbox command is launched
through a small supervisor process that applies the configured isolation policy.

## Features

- Process isolation with `bubblewrap`
- Static policy-based filesystem access rules
- Configurable sandbox backing workspace through `sandbox_workspace`
- Optional network isolation with Linux network namespaces and firewall rules
- Namespace and Linux capability controls
- Landlock filesystem enforcement when supported by the kernel
- Seccomp syscall filtering
- Python and JavaScript execution support when the corresponding runtimes exist
- Audit logging and persisted sandbox lifecycle state
- Inference Privacy Proxy for LLM API request routing with automatic API key injection

## Architecture

- `server`
  - FastAPI app that manages sandbox lifecycle, policy loading, audit logs, and
    API routing.
- `server/runtime`
  - Runtime adapter that starts one supervisor process per sandbox command.
- `server/proxy_manager`
  - Manages inference privacy proxies for LLM API routing with API key injection.
- `server/policy_reader`
  - Shared policy file reader for sandbox and proxy managers.
- `supervisor`
  - Per-command launcher that translates the effective policy into
    `bubblewrap`, Landlock, seccomp, and namespace settings.
- `proxy`
  - HTTP-aware inference privacy proxy with path-based routing and API key
    injection (supports OpenAI and Anthropic formats).
- `models`
  - Pydantic models for policies, sandboxes, API responses, and common status
    structures.

## Requirements

- Linux
- Python 3.11+
- `bubblewrap`
- `iproute2`, `iptables`, and `nftables` when `network.mode: isolated` is used
- Kernel support for Landlock and seccomp when those features are enabled
- `nodejs` if JavaScript execution is needed

On Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y bubblewrap iproute2 iptables nftables python3-pip python3-venv nodejs
```

## Install From Source

```bash
cd jiuwenclaw/jiuwenbox
uv venv
source .venv/bin/activate
uv sync
uv pip install --upgrade pip build
python3 -m build --wheel
uv pip install ./dist/jiuwenbox*.whl
```

## Start The Server

### Local Start

Set the default policy path and start the installed service via the venv
Python:

```bash
sudo env \
  JIUWENBOX_POLICY_PATH="$(pwd)/configs/default-policy.yaml" \
  ./.venv/bin/python -m uvicorn jiuwenbox.server.app:app --host 0.0.0.0 --port 8321 --log-level debug
```

Use another policy or port by changing the environment variable or uvicorn
arguments:

```bash
sudo env \
  JIUWENBOX_POLICY_PATH="$(pwd)/configs/jiuwenclaw-policy.yaml" \
  ./.venv/bin/python -m uvicorn jiuwenbox.server.app:app --host 0.0.0.0 --port 9000 --log-level debug
```

The selected policy path is read from:

```bash
JIUWENBOX_POLICY_PATH=/absolute/path/to/policy.yaml
```

The port can also be set through `JIUWENBOX_PORT` when your process manager
uses it to render the uvicorn command:

```bash
JIUWENBOX_PORT=9000
```

### Docker Start

Build the image:

```bash
cd jiuwenclaw/jiuwenbox/scripts
sudo ./build_docker.sh
```

Run with the default policy:

```bash
sudo ./run_docker.sh
```

### Unix Domain Socket Deployment

The management HTTP API can be served over a Unix Domain Socket instead of
TCP (one-of-two per process). HTTP/1.1 framing, routes and payloads stay
identical; only the transport changes. UDS is useful for same-host agents,
filesystem-permission-based access control, and avoiding loopback port
conflicts.

Listen address is controlled by a single env var:

```bash
JIUWENBOX_LISTEN=tcp://0.0.0.0:8321               # default, historical behavior
JIUWENBOX_LISTEN=unix:///run/jiuwenbox/jiuwenbox.sock  # switch to UDS (absolute path required)
```

Start locally on UDS (same two rules as the ⚠️ in *Local Start*:
`sudo env` for envs, absolute `./.venv/bin/` paths for binaries):

```bash
sudo env \
  JIUWENBOX_POLICY_PATH="$(pwd)/configs/default-policy.yaml" \
  JIUWENBOX_LISTEN=unix:///run/jiuwenbox/jiuwenbox.sock \
  ./.venv/bin/python -m jiuwenbox.server.launcher

# or, with the entry script installed by uv sync / pip install:
sudo env JIUWENBOX_LISTEN=unix:///run/jiuwenbox/jiuwenbox.sock \
  ./.venv/bin/jiuwenbox-server
```

Docker deployment on UDS:

```bash
mkdir -p /tmp/jiuwenbox-sock

sudo env \
  JIUWENBOX_LISTEN=unix:///run/jiuwenbox/jiuwenbox.sock \
  JIUWENBOX_UDS_HOST_DIR=/tmp/jiuwenbox-sock \
  ./run_docker.sh configs/default-policy.yaml
```

`run_docker.sh` skips the management-API TCP port mapping under UDS mode and
bind-mounts the host socket directory into the container; **the proxy port
`${JIUWENBOX_PROXY_PORT:-8322}` is still mapped as TCP** because the
Inference Privacy Proxy is an independent TCP listener.

Reach the server:

```bash
curl --unix-socket /tmp/jiuwenbox-sock/jiuwenbox.sock http://localhost/health
jiuwenbox --base-url unix:///tmp/jiuwenbox-sock/jiuwenbox.sock health
JIUWENBOX_URL=unix:///tmp/jiuwenbox-sock/jiuwenbox.sock jiuwenbox sandbox ls

# pytest in dual transport mode (operator launches the matching server first)
pytest tests/integration --server-endpoint=tcp://127.0.0.1:8321
pytest tests/integration --server-endpoint=unix:///tmp/jiuwenbox-sock/jiuwenbox.sock
```

UDS-related env vars:

| Variable | Default | Notes |
| --- | --- | --- |
| `JIUWENBOX_LISTEN` | `tcp://0.0.0.0:8321` | Management API listen URI; accepts `tcp://host:port` or `unix:///abs/socket/path`. Under UDS mode `JIUWENBOX_PORT` is ignored. |
| `JIUWENBOX_UDS_MODE` | `0666` | UDS socket file permissions (octal string). The Docker default is permissive so a non-root host user can connect; for multi-tenant / hardened deployments set `0660` and pass `docker run --user $(id -u):$(id -g)`. |
| `JIUWENBOX_UDS_HOST_DIR` | `/tmp/jiuwenbox-sock` | Host directory bind-mounted by `run_docker.sh` to expose the socket. |
| `JIUWENBOX_UDS_CONTAINER_DIR` | `/run/jiuwenbox` | Container-side mount point; must match the directory in `JIUWENBOX_LISTEN`'s socket path. |

### Persisting the audit log (`--save-logs DIR`)

**By default jiuwenbox writes no log files at all.** Audit events
surface only on the standard Python logger at ``DEBUG`` level, sandbox
daemon and background-exec stdout/stderr go straight to ``/dev/null``,
and ``/api/v1/sandboxes/{id}/logs`` returns the empty string. This
keeps a fresh install from creating files under ``$HOME`` and prevents
long-running servers from filling the disk silently.

Pass `--save-logs DIR` (or set `JIUWENBOX_SAVE_LOGS_DIR=DIR`) to opt
into per-sandbox **audit log** persistence. Files are **kept after the
sandbox is gone**, which is the shape you want for offline inspection,
log shipping, or postmortem debugging.

> Note: the historical per-sandbox `runtime.log` and `runtime.bg-N.log`
> files (raw daemon and background-exec stdout/stderr) were removed.
> The audit log already carries the truncated per-command stdout/stderr,
> which has proven to be enough for routine debugging while letting us
> drop a class of "two files for the same thing" foot-guns. If you do
> need the full raw byte stream, run the container with `docker run -it`
> so the bwrap output streams to your terminal.

Every operation produces **a single row** in the audit JSONL, emitted
after the call returns so the payload carries both intent (command,
path) and outcome (exit_code, stdout/stderr, error). Reading just the
audit file is enough to answer "did this command succeed?":

| `event_type` | Key fields |
| --- | --- |
| `exec_command` | `command`, `workdir`, `background?`, `ok`, `exit_code`, `stdout`, `stderr`, `duration_ms`, `error?` (stdout/stderr are tail-truncated to 4 KiB; overflow is annotated `[truncated, total N chars]`. Background exec records `started`/`pid` instead of `exit_code`/`stdout`/`stderr`.) |
| `file_transfer` | `direction` (upload/download), `sandbox_path`, `size`, `ok`, `duration_ms`, `path` (`ipc` vs `exec_fallback`), `error?` |

The filename layout is `{sandbox_id}-{ISO8601-basic-timestamp}.audit.log`.
The timestamp is captured the first time a given sandbox writes an
audit event and reused for the rest of that sandbox's lifetime:

```
<DIR>/
  └── 9284a4bf-870-20260515T112345.audit.log   # structured JSONL
```

The basic ISO 8601 layout (`%Y%m%dT%H%M%S`) sorts lexicographically
the same way it sorts chronologically; combined with the sandbox_id
prefix, `ls 9284a4bf-870-*` gives you every audit file for that
sandbox in boot order.

Local launch:

```bash
sudo env \
  JIUWENBOX_POLICY_PATH="$(pwd)/configs/default-policy.yaml" \
  ./.venv/bin/jiuwenbox-server --save-logs /var/log/jiuwenbox

# Equivalent via env:
sudo env \
  JIUWENBOX_POLICY_PATH="$(pwd)/configs/default-policy.yaml" \
  JIUWENBOX_SAVE_LOGS_DIR=/var/log/jiuwenbox \
  ./.venv/bin/jiuwenbox-server
```

Docker launch: pass `--save-logs DIR` (or set
`JIUWENBOX_SAVE_LOGS_HOST_DIR=DIR`) and `run_docker.sh` will bind-mount
`DIR` onto `JIUWENBOX_SAVE_LOGS_CONTAINER_DIR` (default
`/var/log/jiuwenbox`) and inject `JIUWENBOX_SAVE_LOGS_DIR=<container
path>` into the launcher — no `Dockerfile` change required. The CLI
flag and the env var are equivalent; the flag wins when both are
present:

```bash
# CLI flag (recommended)
sudo ./run_docker.sh --save-logs /tmp/jiuwenbox-logs

# Equivalent env-var form (kept for backward compatibility)
sudo env JIUWENBOX_SAVE_LOGS_HOST_DIR=/tmp/jiuwenbox-logs ./run_docker.sh

ls /tmp/jiuwenbox-logs
# 9284a4bf-870-20260515T112345.audit.log
```

| Variable | Default | Notes |
| --- | --- | --- |
| `JIUWENBOX_SAVE_LOGS_DIR` | _unset_ | Target audit-log directory inside the container/process. Unset means **no log files at all** (the new default — see above). The launcher resolves `--save-logs` / the env to an absolute path before writing this back. |
| `JIUWENBOX_SAVE_LOGS_HOST_DIR` | _unset_ | `run_docker.sh` only: host-side directory (env-var form of `--save-logs DIR`). Empty disables persistence. When set, the script `mkdir -p`s it, bind-mounts it into the container, and exports `JIUWENBOX_SAVE_LOGS_DIR`. |
| `JIUWENBOX_SAVE_LOGS_CONTAINER_DIR` | `/var/log/jiuwenbox` | `run_docker.sh` mount point inside the container. Override only if something else already owns this path inside the image. |

## Policy Files

The server loads one static default policy at startup. Policy dynamic update is
not enabled.

Important fields:

- `sandbox_workspace`
  - Host directory used for server-managed sandbox backing storage.
  - The value must be absolute after `~` and environment variables are expanded.
- `filesystem_policy.directories`
  - Directories created by the server and bound into each sandbox for its
    lifecycle.
- `filesystem_policy.read_only`
  - Sandbox-visible paths granted read-only access. These entries do not mount
    host paths by themselves.
- `filesystem_policy.read_write`
  - Sandbox-visible paths granted read-write access. Use `directories` or
    `bind_mounts` to make the paths exist inside the sandbox.
- `filesystem_policy.bind_mounts`
  - Explicit host-to-sandbox bind mounts.
- `filesystem_policy.device`
  - Explicit device nodes exposed inside the sandbox with `bwrap --dev-bind`.

Path fields support shell-style expansion such as `~` and environment
variables.

Minimal example:

```yaml
version: 1
name: "example"
sandbox_workspace: "/sandbox"

filesystem_policy:
  directories:
    - path: "/tmp"
      permissions: "1777"
  read_only:
    - "/bin"
    - "/sbin"
    - "/usr"
    - "/lib"
    - "/lib64"
    - "/etc"
  read_write:
    - "/tmp"
  bind_mounts:
    - host_path: "/bin"
      sandbox_path: "/bin"
      mode: "ro"
    - host_path: "/sbin"
      sandbox_path: "/sbin"
      mode: "ro"
    - host_path: "/usr"
      sandbox_path: "/usr"
      mode: "ro"
    - host_path: "/lib"
      sandbox_path: "/lib"
      mode: "ro"
    - host_path: "/lib64"
      sandbox_path: "/lib64"
      mode: "ro"
    - host_path: "/etc/resolv.conf"
      sandbox_path: "/etc/resolv.conf"
      mode: "ro"
    - host_path: "/etc/hosts"
      sandbox_path: "/etc/hosts"
      mode: "ro"
    - host_path: "/etc/nsswitch.conf"
      sandbox_path: "/etc/nsswitch.conf"
      mode: "ro"
    - host_path: "/etc/host.conf"
      sandbox_path: "/etc/host.conf"
      mode: "ro"
    - host_path: "/etc/ssl/certs"
      sandbox_path: "/etc/ssl/certs"
      mode: "ro"
    - host_path: "/etc/ssl/openssl.cnf"
      sandbox_path: "/etc/ssl/openssl.cnf"
      mode: "ro"
  device:
    - host_path: "/dev/null"
      sandbox_path: "/dev/null"

process:
  run_as_user: sandbox
  run_as_group: sandbox

namespace:
  user: true
  pid: true
  ipc: true
  cgroup: true
  uts: true

capabilities:
  add: []
  drop: []

landlock:
  compatibility: best_effort

syscall:
  x86_64:
    blocked:
      - "ptrace"
      - "mount"
      - "umount2"
      - "reboot"
      - "kexec_load"
  arm64:
    blocked:
      - "ptrace"
      - "mount"
      - "umount2"
      - "reboot"
      - "kexec_load"

network:
  mode: isolated
  egress:
    default: allow
    allowed_domains: []
    blocked_domains: []
    allowed_ips:
      - "127.0.0.1/32"
      - "::1/128"
    blocked_ips: []
    allowed_ports:
      - 443
      - 80
    blocked_ports:
      - 22
  ingress:
    default: deny
    allowed_domains: []
    blocked_domains: []
    allowed_ips:
      - "127.0.0.1/32"
      - "::1/128"
    blocked_ips: []
    allowed_ports: []
    blocked_ports:
      - 22
```

## Enabling jiuwenbox from jiuwenclaw's config file

jiuwenclaw decides **whether the sandbox is on, which jiuwenbox to talk to, whether to spawn its own jiuwenbox subprocess, and which policy file to use** via the `sandbox` section of its `config.yaml`. The TUI's `/sandbox` command writes back to the same section, so you can also pre-populate it by hand.

### Configuration schema

```yaml
sandbox:
  # -- Endpoint & type --
  url: "http://127.0.0.1:8321"      # jiuwenbox HTTP endpoint; TCP uses http://, UDS uses unix:///abs/socket/path
  type: "jiuwenbox"                 # sandbox provider name; currently only "jiuwenbox"

  # -- Startup & policy --
  startup_mode: "internal"          # internal = agent-server spawns jiuwenbox-server; external = you start it yourself
  policy_file: "code-agent-policy.yaml"   # bare name -> jiuwenbox/configs/<name>; otherwise an absolute / explicit path
  preserve_file_sharing_mode: "mount"     # only `mount` is supported; any other value is rejected

  # -- Runtime (also managed by the /sandbox TUI command) --
  enabled: true                     # whether sandbox mode is on
  excluded_commands:                # shell globs whose matches run locally instead of in the sandbox
    - "git *"
  files:                            # user-configured write policy; auto-managed paths are injected by the server, no need to repeat them here
    allow: []
    deny: []
```

Field reference:

| Field | Values | Default | Notes |
| --- | --- | --- | --- |
| `sandbox.url` | URL string | `http://127.0.0.1:8321` | jiuwenbox management API endpoint. TCP: `http://host:port`; UDS: `unix:///abs/socket/path` (mirrors `JIUWENBOX_LISTEN`). |
| `sandbox.type` | string | `jiuwenbox` | Sandbox provider name. Currently jiuwenclaw only wires up `jiuwenbox`. |
| `sandbox.startup_mode` | `internal` / `external` | `internal` | `internal`: agent-server spawns `jiuwenbox-server` at boot and persists the effective `url` (auto-picks a free port if the configured one is busy). `external`: agent-server never touches jiuwenbox; you must start it yourself per the top of this README. |
| `sandbox.policy_file` | filename or path | `code-agent-policy.yaml` | Bare filename → resolved relative to `jiuwenbox/configs/`; otherwise expanded (`~`, `$VAR`) and used verbatim. **Only honored under `startup_mode=internal`**; in `external` mode the policy is chosen by whoever started jiuwenbox-server (via `JIUWENBOX_DEFAULT_POLICY_PATH`). |
| `sandbox.preserve_file_sharing_mode` | `mount` | `mount` | Intrinsic files (`AGENT.md` etc.) and `project_dir` are bind-mounted, with `project_dir/config/config.yaml` auto-added to `deny_write`. Writing any other value is rejected. |
| `sandbox.enabled` | bool | `false` | When true, agent rebuilds route tools through the sandbox provider; toggled by `/sandbox enable`. |
| `sandbox.excluded_commands` | list[str] | `[]` | Shell globs matched against the **full command string**; a match makes that single call run locally instead of in the sandbox. |
| `sandbox.files.allow` / `sandbox.files.deny` | list | `[]` | User-configured write policy. The effective set shown by `/sandbox status` is `auto_managed ∪ user_configured`; see [the `/sandbox` design doc](../../agent-core/docs/en/2.Development%20Guide/Sandbox%20and%20sandbox%20command.md). |

### Two typical deployment shapes

#### Shape A: `startup_mode: internal` (agent-server spawns jiuwenbox for you)

Good for local development and single-host deployments. Drop this into `config.yaml`:

```yaml
sandbox:
  url: "http://127.0.0.1:8321"
  type: "jiuwenbox"
  startup_mode: "internal"
  policy_file: "code-agent-policy.yaml"   # picked up from jiuwenbox/configs/
  enabled: true
```

At boot the agent-server will:

1. Resolve `policy_file` to a host absolute path (bare name → `jiuwenbox/configs/<name>`; otherwise expand `~` / `$VAR` and use as-is).
2. Probe the port in `url`; if taken, switch to a free one and persist the new `url` back into `config.yaml`, so `/sandbox status` shows the real port.
3. Spawn `jiuwenbox-server` with the resolved policy path. On failure, agent-server logs the last 10 lines of stderr; you can retry from the TUI via `/sandbox enable`.

#### Shape B: `startup_mode: external` (you start jiuwenbox-server yourself)

Good when jiuwenbox lives on a different host / container, or when jiuwenclaw should never escalate to root. Example:

```yaml
sandbox:
  url: "http://10.0.0.5:8321"   # or unix:///run/jiuwenbox/jiuwenbox.sock
  type: "jiuwenbox"
  startup_mode: "external"
  enabled: true
```

Under this mode agent-server **does not** try to spawn jiuwenbox, and `sandbox.policy_file` has **no effect** (the policy is whatever you passed to `jiuwenbox-server` via `JIUWENBOX_DEFAULT_POLICY_PATH`). See [`Start The Server`](#start-the-server) and [`Unix Domain Socket Deployment`](#unix-domain-socket-deployment) above for how to start jiuwenbox-server in TCP or UDS mode.

For cross-host setups, the jiuwenbox host has to be able to reach jiuwenclaw's intrinsic agent files on the same host paths: `preserve_file_sharing_mode` is now fixed to `mount`, so jiuwenclaw bind-mounts the intrinsic files (`AGENT.md`, `HEARTBEAT.md`, `IDENTITY.md`, `SOUL.md`, `USER.md`, `memory/daily_memory/`) and `project_dir` into the sandbox. Make the relevant directories visible on the jiuwenbox machine (via shared filesystem, container volume, etc.) and confirm the policy allows writes into them (the bundled `jiuwenbox/configs/code-agent-policy.yaml` already does).


## Inference Privacy Proxy

The inference privacy proxy enables secure LLM API access from edge servers:

- Path-based routing to different LLM providers (OpenAI, Anthropic, custom)
- Automatic API key injection (OpenAI `Authorization: Bearer`, Anthropic `X-Api-Key`)
- Hot-pluggable via REST API (create/start/stop/update/delete)
- Configured via policy YAML or dynamically through API

**Architecture**:

One global proxy process listens on a single host:port.

**Privacy routes default to `listen_port=0` (disabled)**. When enabled, both `listen_host` (IP address) and `listen_port` must be configured.

Routes are differentiated by `path_prefix` (forwarding rules). Each route has **independent state** (`running` = enabled forwarding; `stopped` = disabled).

**Creating routes via API requires valid `listen_host` and `listen_port > 0`**, otherwise returns an error.

### Proxy Configuration

Policy YAML configuration schema:

```yaml
inference_privacy_proxies:
  listen_host: ipaddress, IP address to bind  # MUST
  listen_port: number, listen port             # MUST, non-zero enables proxy

  # OPTIONAL, can be managed via REST API after startup
  routes:
   - path_prefix: str, path name for forwarding rule
      target_endpoint: URL, target endpoint
      api_key: str, api key to inject when forwarding
      skip_cert_verify: boolean, skip cert verify for self-signed https targets, debug only
```

### API Key Injection

- OpenAI: Replace `Authorization: Bearer <placeholder>` with actual key
- Anthropic: Replace `X-Api-Key: <placeholder>` with actual key

### Configuration Example

`Note: The network endpoints https://api.openai.com and http://192.168.1.100:9000 are examples only`

#### Policy YAML Example

```yaml
inference_privacy_proxies:

  listen_host: "127.0.0.1"
  listen_port: 8080
  
  routes:
    - path_prefix: "openai"
      target_endpoint: "https://api.openai.com"
      api_key: "sk_sandbox_managed_openai_key"
   - path_prefix: "custom"
      target_endpoint: "http://192.168.1.100:9000"
      api_key: "sk_sandbox_managed_custom_key"
```

For edge servers, use `listen_host: "0.0.0.0"` to accept connections from all interfaces.

#### Forwarding Example

```text
Client request:  POST http://127.0.0.1:8322/openai/v1/chat/completions -H "Authorization: Bearer sk_fake_key"
Proxy forwards:  POST https://api.openai.com/v1/chat/completions       -H "Authorization: Bearer sk_sandbox_managed_openai_key"

Client request:  POST http://127.0.0.1:8322/custom/v1/chat/completions -H "Authorization: Bearer sk_fake_key"
Proxy forwards:  POST http://192.168.1.100:9000/v1/chat/completions    -H "Authorization: Bearer sk_sandbox_managed_custom_key"
```

#### jiuwenclaw Configuration Example

| Config    | Old Value                     | New Value                          |
| --------- | ----------------------------- | ---------------------------------- |
| api_base  | http://192.168.1.100:9000/v1/ | http://127.0.0.1:8322/custom/v1/   |
| api_key   | sk_sandbox_managed_custom_key | sk_fake_key                        |

## Run Integration Tests

`./tests/test.sh default` runs `test_server_api_default.py` and
`test_cli_default.py` together, exercising both the server HTTP API and the
jiuwenbox CLI. Use `--server-endpoint=URI` to switch between transports;
**the transport is inferred from the URI shape**, so there's no separate
flag to maintain in sync:

```bash
# TCP (default, equivalent to --server-endpoint=http://127.0.0.1:8321; the
# server should be launched with default-policy.yaml as its security policy)
./tests/test.sh default

# Custom TCP listener (a bare host:port gets http:// prepended automatically)
./tests/test.sh default --server-endpoint=http://127.0.0.1:18321
./tests/test.sh default --server-endpoint=127.0.0.1:18321

# UDS: pass the absolute socket path as a unix:// URL
./tests/test.sh default --server-endpoint=unix:///tmp/jiuwenbox.sock
./tests/test.sh default --server-endpoint=unix:///tmp/jiuwenbox-sock/jiuwenbox.sock
```

`test.sh` does **not** start the server; launch jiuwenbox first on the
selected transport (TCP with `JIUWENBOX_LISTEN=tcp://0.0.0.0:8321` or a
custom port, UDS with `JIUWENBOX_LISTEN=unix:///...`).

Run specific test-case:

```bash
python3 -m pytest tests/integration/test_server_api_default.py::TestPolicyEnforcement::test_network_mode_isolated_blocks_http_requests -s --server-endpoint 127.0.0.1:8321
```

### Performance Tests

Run the office-workload performance suite:

```bash
./tests/test.sh performance --server-endpoint 127.0.0.1:8321
```

Tune sandbox count, per-sandbox concurrency, and per-task loop count:

```bash
./tests/test.sh performance \
  --sandbox-count 2 \
  --concurrency 16 \
  --loop 8 \
  --server-endpoint 127.0.0.1:8321
```

The script maps these arguments to environment variables used by the performance
fixtures:

| Script argument | Environment variable | Default |
| --------------- | -------------------- | ------- |
| `--sandbox-count` | `JIUWENBOX_PERF_SANDBOX_COUNT` | `1` |
| `--concurrency` | `JIUWENBOX_PERF_CONCURRENCY` | `4` |
| `--loop` | `JIUWENBOX_PERF_LOOP` | `8` |

### Real LLM Integration Tests

To run real LLM integration tests, set the following environment variables. These tests are skipped by default if the environment variables are not set.:

```bash
export JIUWENBOX_TEST_LLM_ENDPOINT="https://api.openai.com"
export JIUWENBOX_TEST_LLM_API_KEY="sk_sandbox_managed_key"
export JIUWENBOX_TEST_LLM_MODEL="YOUR_MODEL"
```

## Notes

- Restart the server after changing the startup policy file.
- Existing sandboxes keep the policy that was written for them when they were
  created.
- Command stderr is returned as command output by the `/exec` API; server-side
  diagnostics should use debug logging when they would otherwise pollute
  command stderr.

## CLI

`jiuwenbox` ships a single-file Python CLI client wrapping the HTTP API
documented in [`docs/jiuwenbox_server_api.md`](docs/jiuwenbox_server_api.md).

After `pip install` it is exposed as the `jiuwenbox` executable; from source
you can also run it as `python -m jiuwenbox.cli.jiuwenbox`.

```bash
# Health
jiuwenbox health

# Sandbox lifecycle
ID=$(jiuwenbox sandbox create --output plain)
jiuwenbox sandbox exec "$ID" -- python3 -c 'print("hi")'
jiuwenbox sandbox upload "$ID" ./data.csv /tmp/data.csv
jiuwenbox sandbox download "$ID" /tmp/result.json - | jq .
jiuwenbox sandbox ls --output table
jiuwenbox sandbox rm "$ID" --yes

# Policy
jiuwenbox policy get "$ID"

# Proxies
jiuwenbox proxy create --prefix /openai --target https://api.openai.com --api-key sk-xxx
jiuwenbox proxy logs openai --lines 50
```

Global flags:

| Flag | Default | Env var | Description |
| --- | --- | --- | --- |
| `--base-url` | `http://127.0.0.1:8321` | `JIUWENBOX_URL` | Server endpoint. Accepts `http://host:port` or `unix:///abs/socket/path` |
| `--timeout` | `30` | `JIUWENBOX_TIMEOUT` | HTTP timeout seconds |
| `--output / -o` | `json` | – | `json` \| `table` \| `plain` |
| `--verbose / -v` | off | – | Debug logging on stderr |
| `--no-color` | off | `NO_COLOR` | Disable ANSI colors on stderr |

Exit codes: `0` success / sandbox exec returned 0, `1` HTTP 4xx/5xx, `2`
connection failure, `3` local argument or file error, `130` Ctrl+C; the
`sandbox exec` subcommand transparently propagates the in-sandbox process
exit code.

## License

Apache-2.0
