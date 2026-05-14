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

## Install

```bash
cd jiuwenclaw/jiuwenbox
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip build
python3 -m build --wheel
python3 -m pip install dist/jiuwenbox-*.whl
```

## Start The Server

### Local Start

Set the default policy path and start the installed service with `python -m`:

```bash
export JIUWENBOX_POLICY_PATH="$(pwd)/configs/default-policy.yaml"
sudo -E .venv/bin/python -m uvicorn jiuwenbox.server.app:app --host 0.0.0.0 --port 8321 --log-level debug
```

Use another policy or port by changing the environment variable or uvicorn
arguments:

```bash
export JIUWENBOX_POLICY_PATH="$(pwd)/configs/jiuwenclaw-policy.yaml"
sudo -E .venv/bin/python -m uvicorn jiuwenbox.server.app:app --host 0.0.0.0 --port 9000 --log-level debug
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

Run one policy-specific integration suite:

```bash
./tests/test.sh default # jiuwenbox runs the service using default-policy.yaml as the security policy.
./tests/test.sh yuanrong # jiuwenbox runs the service using yuanrong.yaml with proxy enabled on port 8322.
```

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

## License

Apache-2.0
