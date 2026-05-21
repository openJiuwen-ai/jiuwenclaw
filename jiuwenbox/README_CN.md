# jiuwenbox

`jiuwenbox` 是一个轻量级 Linux 沙箱服务，用于在分层隔离环境中运行
agent 工具和代码片段。

它提供一个 FastAPI 服务，用于管理沙箱生命周期、文件传输、文件
列表/搜索以及命令执行。每个沙箱命令都会通过一个小型 supervisor
进程启动，由 supervisor 根据配置好的隔离策略应用沙箱限制。

## 功能特性

- 基于 `bubblewrap` 的进程隔离
- 基于静态 policy 的文件系统访问控制
- 通过 `sandbox_workspace` 配置沙箱后端工作目录
- 可选的 Linux 网络命名空间和防火墙网络隔离
- 命名空间和 Linux capability 控制
- 在内核支持时启用 Landlock 文件系统约束
- Seccomp 系统调用过滤
- 在运行时存在时支持 Python 和 JavaScript 代码执行
- 审计日志和持久化的沙箱生命周期状态
- 推理隐私代理，用于 LLM API 请求路由和自动 API 密钥注入

## 架构

- `server`
  - FastAPI 应用，负责沙箱生命周期管理、policy 加载、审计日志和 API 路由。
- `server/runtime`
  - 运行时适配层，负责为每个沙箱命令启动一个 supervisor 进程。
- `server/proxy_manager`
  - 管理推理隐私代理，用于 LLM API 路由和 API 密钥注入。
- `server/policy_reader`
  - 共享 policy 文件读取器，供沙箱和代理管理器使用。
- `supervisor`
  - 每条命令的启动器，负责将生效的 policy 转换为 `bubblewrap`、Landlock、
    seccomp 和命名空间配置。
- `proxy`
  - HTTP 推理隐私代理，支持路径路由和 API 密钥注入（支持 OpenAI 和 Anthropic 格式）。
- `models`
  - 基于 Pydantic 的 policy、沙箱、API 响应和通用状态结构模型。

## 环境要求

- Linux
- Python 3.11+
- `bubblewrap`
- 使用 `network.mode: isolated` 时需要 `iproute2`、`iptables` 和 `nftables`
- 启用 Landlock 和 seccomp 时需要内核支持对应能力
- 如果需要执行 JavaScript，则需要 `nodejs`

Ubuntu 安装示例：

```bash
sudo apt-get update
sudo apt-get install -y bubblewrap iproute2 iptables nftables python3-pip python3-venv nodejs
```

## 从源码安装

```bash
cd jiuwenclaw/jiuwenbox
uv venv
source .venv/bin/activate
uv sync
uv pip install --upgrade pip build
python3 -m build --wheel
uv pip install ./dist/jiuwenbox*.whl
```

## 启动服务

### 本地启动

设置默认 policy 路径，并通过 venv 里的 python 启动已安装的服务：

```bash
sudo env \
  JIUWENBOX_POLICY_PATH="$(pwd)/configs/default-policy.yaml" \
  ./.venv/bin/python -m uvicorn jiuwenbox.server.app:app --host 0.0.0.0 --port 8321 --log-level debug
```

如需使用其他 policy 或端口，可修改环境变量或 uvicorn 参数：

```bash
sudo env \
  JIUWENBOX_POLICY_PATH="$(pwd)/configs/jiuwenclaw-policy.yaml" \
  ./.venv/bin/python -m uvicorn jiuwenbox.server.app:app --host 0.0.0.0 --port 9000 --log-level debug
```

服务会从以下环境变量读取默认 policy 路径：

```bash
JIUWENBOX_POLICY_PATH=/absolute/path/to/policy.yaml
```

如果进程管理器会使用环境变量渲染 uvicorn 命令，也可以设置：

```bash
JIUWENBOX_PORT=9000
```

### Docker 启动

构建镜像：

```bash
cd jiuwenclaw/jiuwenbox/scripts
sudo ./build_docker.sh
```

使用默认 policy 运行：

```bash
sudo ./run_docker.sh
```

### 通过 Unix Domain Socket 部署

jiuwenbox 支持把管理 HTTP API 跑在 Unix Domain Socket 上（与 TCP 二选一），
适用于同主机 agent 进程访问、需要文件系统权限控制访问者、或想避开
loopback 端口冲突的场景。上层协议仍是 HTTP/1.1，路由 / 请求体 / 响应都
与 TCP 模式完全一致。

监听地址由统一的环境变量 `JIUWENBOX_LISTEN` 控制，取以下两种形式之一：

```bash
JIUWENBOX_LISTEN=tcp://0.0.0.0:8321               # 默认, 行为与历史一致
JIUWENBOX_LISTEN=unix:///run/jiuwenbox/jiuwenbox.sock  # 切到 UDS, 路径必须绝对
```

本地启动 UDS server（同上节 ⚠️ 的两条规则：`sudo env` 注 env、`./.venv/bin/`
绝对路径）：

```bash
sudo env \
  JIUWENBOX_POLICY_PATH="$(pwd)/configs/default-policy.yaml" \
  JIUWENBOX_LISTEN=unix:///run/jiuwenbox/jiuwenbox.sock \
  ./.venv/bin/python -m jiuwenbox.server.launcher

# 或直接用 uv sync / pip install 装好的入口脚本:
sudo env JIUWENBOX_LISTEN=unix:///run/jiuwenbox/jiuwenbox.sock \
  ./.venv/bin/jiuwenbox-server
```

Docker 部署 UDS：

```bash
mkdir -p /tmp/jiuwenbox-sock

sudo env \
  JIUWENBOX_LISTEN=unix:///run/jiuwenbox/jiuwenbox.sock \
  JIUWENBOX_UDS_HOST_DIR=/tmp/jiuwenbox-sock \
  ./run_docker.sh configs/default-policy.yaml
```

`run_docker.sh` 在 UDS 模式下会自动跳过管理 API 的 TCP 端口映射、把宿主
socket 目录挂进容器；**代理端口 `${JIUWENBOX_PROXY_PORT:-8322}` 仍按 TCP
映射**——Inference Privacy Proxy 是独立 TCP listener，与管理 API 传输无关。

接入示例：

```bash
# curl
curl --unix-socket /tmp/jiuwenbox-sock/jiuwenbox.sock http://localhost/health

# jiuwenbox CLI
jiuwenbox --base-url unix:///tmp/jiuwenbox-sock/jiuwenbox.sock health
JIUWENBOX_URL=unix:///tmp/jiuwenbox-sock/jiuwenbox.sock jiuwenbox sandbox ls

# pytest 双通路 (操作者先各自起好对应的 server)
pytest tests/integration --server-endpoint=tcp://127.0.0.1:8321
pytest tests/integration --server-endpoint=unix:///tmp/jiuwenbox-sock/jiuwenbox.sock
```

UDS 相关环境变量：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `JIUWENBOX_LISTEN` | `tcp://0.0.0.0:8321` | 管理 API 监听 URI；接受 `tcp://host:port` 或 `unix:///abs/socket/path`。UDS 模式下 `JIUWENBOX_PORT` 被忽略。 |
| `JIUWENBOX_UDS_MODE` | `0666` | UDS socket 文件权限 (八进制字符串)。Docker 场景下宿主与容器内 uvicorn uid 通常不同，默认放开；多租户 / 强隔离场景建议显式 `JIUWENBOX_UDS_MODE=0660` 并 `docker run --user $(id -u):$(id -g)` 收紧。 |
| `JIUWENBOX_UDS_HOST_DIR` | `/tmp/jiuwenbox-sock` | `run_docker.sh` 把宿主 socket 目录挂载到容器内的位置。 |
| `JIUWENBOX_UDS_CONTAINER_DIR` | `/run/jiuwenbox` | 容器内挂载点，必须与 `JIUWENBOX_LISTEN` 里 socket 路径所在的目录一致。 |

### 持久化审计日志（`--save-logs DIR`）

**默认情况下 jiuwenbox 不会写任何日志文件**：审计事件只在 Python 标
准 logger 的 `DEBUG` 级别出现，沙箱 daemon 与后台 exec 的 stdout/stderr
直接送到 `/dev/null`，`/api/v1/sandboxes/{id}/logs` 返回空字符串。这样
保证一台新装的机器不会在 `$HOME` 下悄悄留下任何文件，也不会因为长期
运行的服务把磁盘写满。

传 `--save-logs DIR`（或环境变量 `JIUWENBOX_SAVE_LOGS_DIR=DIR`）即可
开启**审计日志**的持久化。文件**销毁沙箱时不再删除**，便于事后离线
分析、滚动归档、外挂到日志收集系统。

> 注意：历史版本曾把沙箱 daemon / 后台 exec 的原始 stdout/stderr 写到
> `runtime.log` / `runtime.bg-N.log` 这一组文件里，现在已经**完全移除**。
> 审计日志里 `exec_command` 事件本身就携带了每条命令截断后的 stdout/stderr
> （默认 4 KiB），日常排障已经够用；如果确实需要看原始字节流，请用
> `docker run -it` 直接看 bwrap 的实时输出。

审计 JSONL 里**每个操作只落一行**，在调用返回后写出，同时携带"做了什么"
和"结果如何"。只看 JSONL 就能回答"这条指令到底成不成功"：

| event_type | 关键字段 |
| --- | --- |
| `exec_command` | `command`, `workdir`, `background?`, `ok`, `exit_code`, `stdout`, `stderr`, `duration_ms`, `error?`（stdout/stderr 默认尾部截断到 4 KiB，超出会标 `[truncated, total N chars]`；后台 exec 时改记 `started/pid` 而不是 `exit_code/stdout/stderr`） |
| `file_transfer` | `direction` (upload/download), `sandbox_path`, `size`, `ok`, `duration_ms`, `path`（`ipc` 还是 `exec_fallback`）, `error?` |

文件命名固定为 `{sandbox_id}-{ISO8601基本时间戳}.audit.log`，时间戳在
该 sandbox 第一次产生事件时确定并复用：

```
<DIR>/
  └── 9284a4bf-870-20260515T112345.audit.log   # 结构化 JSONL
```

ISO 8601 基本格式 (`%Y%m%dT%H%M%S`) 是为了让 `ls` 自然按时间排序；前缀
都是 sandbox_id，所以 `ls 9284a4bf-870-*` 能一次性看到一个沙箱所有
重启的审计文件。

本地启动：

```bash
sudo env \
  JIUWENBOX_POLICY_PATH="$(pwd)/configs/default-policy.yaml" \
  ./.venv/bin/jiuwenbox-server --save-logs /var/log/jiuwenbox

# 或走环境变量, 等价:
sudo env \
  JIUWENBOX_POLICY_PATH="$(pwd)/configs/default-policy.yaml" \
  JIUWENBOX_SAVE_LOGS_DIR=/var/log/jiuwenbox \
  ./.venv/bin/jiuwenbox-server
```

Docker 部署：传 `--save-logs DIR`（或设环境变量
`JIUWENBOX_SAVE_LOGS_HOST_DIR=DIR`），`run_docker.sh` 会自动 bind-mount 到
容器内 `JIUWENBOX_SAVE_LOGS_CONTAINER_DIR`（默认 `/var/log/jiuwenbox`），
并把 `JIUWENBOX_SAVE_LOGS_DIR=<容器路径>` 注入给 launcher，无需改
`Dockerfile`。命令行参数与环境变量等价，两者同时存在时 CLI 参数优先：

```bash
# CLI 参数（推荐）
sudo ./run_docker.sh --save-logs /tmp/jiuwenbox-logs

# 等价的环境变量写法（保留以兼容老脚本）
sudo env JIUWENBOX_SAVE_LOGS_HOST_DIR=/tmp/jiuwenbox-logs ./run_docker.sh

ls /tmp/jiuwenbox-logs
# 9284a4bf-870-20260515T112345.audit.log
```

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `JIUWENBOX_SAVE_LOGS_DIR` | _未设置_ | 容器内 / 进程内的目标审计日志目录；未设置即**完全不写日志文件**（默认）。launcher 会把 `--save-logs` / 环境变量解析为绝对路径写回此变量。 |
| `JIUWENBOX_SAVE_LOGS_HOST_DIR` | _未设置_ | `run_docker.sh` 专用：宿主侧目录（`--save-logs DIR` 的环境变量形式），留空即不开启日志持久化。设置后会自动 `mkdir -p`、bind-mount 到容器，并设置 `JIUWENBOX_SAVE_LOGS_DIR`。 |
| `JIUWENBOX_SAVE_LOGS_CONTAINER_DIR` | `/var/log/jiuwenbox` | `run_docker.sh` 在容器内的挂载点。一般无需修改；若容器内有别的进程占了这个路径再覆盖。 |

## Policy 文件

服务启动时会加载一个静态默认 policy。当前不启用 policy 动态更新功能。

重要字段：

- `sandbox_workspace`
  - 用于服务端管理沙箱后端存储的宿主机目录。
  - 该值在展开 `~` 和环境变量之后必须是绝对路径。
- `filesystem_policy.directories`
  - 由服务端创建并在沙箱生命周期内绑定到沙箱中的目录。
- `filesystem_policy.read_only`
  - 沙箱内授予只读访问权限的路径；这些条目本身不会挂载 host 路径。
- `filesystem_policy.read_write`
  - 沙箱内授予读写访问权限的路径；需要通过 `directories` 或 `bind_mounts`
    让这些路径实际存在于沙箱内。
- `filesystem_policy.bind_mounts`
  - 显式的宿主机到沙箱路径的 bind mount 配置。
- `filesystem_policy.device`
  - 使用 `bwrap --dev-bind` 暴露到沙箱内的显式设备节点。

路径字段支持 shell 风格的展开，例如 `~` 和环境变量。

最小示例：

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

## 在 jiuwenclaw 中通过配置文件启用 jiuwenbox

jiuwenclaw 通过 `config.yaml` 的 `sandbox` 段决定**是否启用沙箱、连接哪台 jiuwenbox、是否自己拉起 jiuwenbox 子进程、用哪个 policy**。一般用 TUI 的 `/sandbox` 命令操作时会自动落盘到这里，但也可以提前在 `config.yaml` 里手写。

### 配置 schema 与字段

```yaml
sandbox:
  # —— 端点 & 类型 ——
  url: "http://127.0.0.1:8321"      # jiuwenbox HTTP 端点；TCP 用 http://，UDS 用 unix:///abs/socket/path
  type: "jiuwenbox"                 # sandbox provider 名；当前固定为 jiuwenbox

  # —— 启动方式 & policy ——
  startup_mode: "internal"          # internal=agent-server 自动拉起 jiuwenbox-server；external=用户自行启动
  policy_file: "code-agent-policy.yaml"   # 仅文件名 → jiuwenbox/configs/<name>；含 / 或绝对路径 → 整路径
  preserve_file_sharing_mode: "mount"     # 仅支持 mount；写入其它值会被服务端拒绝

  # —— 运行时（也可由 /sandbox 命令维护） ——
  enabled: true                     # 是否处于沙箱模式
  excluded_commands:                # shell glob，命中后绕过沙箱在本地执行
    - "git *"
  files:                            # 用户配置的写入策略（auto-managed 路径不需要写在这里，服务端会自动注入）
    allow: []
    deny: []
```

字段说明：

| 字段 | 取值 | 默认 | 说明 |
| --- | --- | --- | --- |
| `sandbox.url` | URL 字符串 | `http://127.0.0.1:8321` | jiuwenbox 管理 API 端点。TCP 用 `http://host:port`；UDS 用 `unix:///abs/socket/path`（与 `JIUWENBOX_LISTEN` 配置的形态一致） |
| `sandbox.type` | 字符串 | `jiuwenbox` | sandbox provider 名。当前 jiuwenclaw 只接通了 `jiuwenbox` |
| `sandbox.startup_mode` | `internal` / `external` | `internal` | `internal`：agent-server 启动时自动 spawn `jiuwenbox-server` 子进程并落盘最终生效的 `url`（端口被占用时自动换端口）；`external`：jiuwenclaw 完全不碰 jiuwenbox 进程，要求按本 README 顶部的方式提前自己启动 |
| `sandbox.policy_file` | 文件名 / 路径 | `code-agent-policy.yaml` | 仅给文件名 → 自动定位到 `jiuwenbox/configs/<name>`；包含 `/` `\` 或 `~` 时按整路径解析。**仅在 `startup_mode=internal` 下生效**——`external` 模式下 policy 由用户自启动时的 `JIUWENBOX_DEFAULT_POLICY_PATH` 决定 |
| `sandbox.preserve_file_sharing_mode` | `mount` | `mount` | intrinsic 文件（`AGENT.md` 等）与 `project_dir` 通过 bind mount 注入沙箱，`project_dir/config/config.yaml` 自动加进 `deny_write`。 写入其它值会被服务端拒绝 |
| `sandbox.enabled` | bool | `false` | 启用后 agent 在重建时会切到 sandbox provider；可用 `/sandbox enable` 触发 |
| `sandbox.excluded_commands` | list[str] | `[]` | shell glob 列表；按**整条命令字符串**匹配，命中后该次调用穿透到本地 |
| `sandbox.files.allow` / `sandbox.files.deny` | list | `[]` | 用户额外配置的写入策略；最终生效集合是 `auto_managed ∪ user_configured`，详见 [`/sandbox` 命令设计文档](../../agent-core/docs/zh/2.开发指南/沙箱与%20sandbox%20命令.md) |

### 两种典型部署方式

#### 方式 A: `startup_mode: internal`（agent-server 帮你拉起 jiuwenbox）

适合本机开发 / 单机部署。直接在 `config.yaml` 里加：

```yaml
sandbox:
  url: "http://127.0.0.1:8321"
  type: "jiuwenbox"
  startup_mode: "internal"
  policy_file: "code-agent-policy.yaml"   # 用 jiuwenbox/configs/ 下的 policy
  enabled: true
```

agent-server 启动时会：

1. 把 `policy_file` 解析为宿主机绝对路径（仅文件名→`jiuwenbox/configs/<name>`；其它路径直接展开 `~` / `$VAR`）。
2. 探测 `url` 里的端口是否可用；冲突就自动换端口，并把最终的 `url` 写回 `config.yaml`，TUI `/sandbox status` 看到的就是真实端口。
3. spawn `jiuwenbox-server`，把 policy 路径传进去；启动失败会写一份 stderr 末尾到日志，TUI 仍能用 `/sandbox enable` 重试。

#### 方式 B: `startup_mode: external`（你自己启动 jiuwenbox-server）

适合需要把 jiuwenbox 跑在独立机器、容器里，或者 jiuwenclaw 进程不便用 root 的场景。

```yaml
sandbox:
  url: "http://10.0.0.5:8321"   # 或 unix:///run/jiuwenbox/jiuwenbox.sock
  type: "jiuwenbox"
  startup_mode: "external"
  enabled: true
```

此模式下 agent-server **不会**尝试拉起 jiuwenbox，`sandbox.policy_file` 也**不生效**（policy 由你启动 jiuwenbox-server 时通过 `JIUWENBOX_DEFAULT_POLICY_PATH` 指定）。jiuwenbox-server 的启动方式见前文 [`启动服务`](#启动服务) 与 [`通过 Unix Domain Socket 部署`](#通过-unix-domain-socket-部署)。

跨机部署要求 jiuwenbox 主机能访问 jiuwenclaw 的固有 agent 文件路径——`preserve_file_sharing_mode` 现在只支持 `mount`，jiuwenclaw 会把 intrinsic 文件（`AGENT.md` / `HEARTBEAT.md` / `IDENTITY.md` / `SOUL.md` / `USER.md` / `memory/daily_memory/`）和 `project_dir` 通过 bind mount 暴露给沙箱，因此目标主机必须能在同样的 host path 下看到这些文件（例如共享文件系统、容器 volume 等）。

## 推理隐私代理

推理隐私代理用于在边缘服务器上安全访问 LLM API：

- 路径路由到不同 LLM 提供商（OpenAI、Anthropic、自定义）
- 自动 API 密钥注入（OpenAI `Authorization: Bearer`、Anthropic `X-Api-Key`）
- 通过 REST API 热插拔（创建/启动/停止/重启/更新/删除）
- 通过 policy YAML 配置或REST API 管理

**架构说明**：

服务端运行一个全局代理进程，监听单一 host:port。

**隐私路由默认 `listen_port=0`（禁用）**，启用时需同时配置 `listen_host`（IP 地址）和 `listen_port`。

通过 `path_prefix`区分路由（转发规则）。**每条路由有独立状态**（`running` = 启用转发流量；`stopped` = 禁用）。

**通过 API 创建路由需 `listen_host` 有效且 `listen_port > 0`**，否则返回错误。

### 代理配置

配置文件yaml文件说明：

```yaml
inference_privacy_proxies:
  listen_host: ipaddress，绑定的 IP 地址  # 必须
  listen_port: number：监听端口号         # 必须，非 0 值启用代理

  # 选填，可在启动后通过RESTAPI管理
  routes:
   - path_prefix: str，转发规则的路径名称
      target_endpoint: URL，目标端点
      api_key: str，转发时用于替换的api key
      skip_cert_verify: boolean，仅当target_endpoint为https且证书为自签名时跳过证书校验，调试用
```

### URL 路由

将
http://\<listening_host\>:\<listening_port\>/\<path_prefix\>/\<api_path\>
转发至
\<target_endpoint\>/\<api_path\>

### API 密钥注入

- OpenAI:     将 `Authorization: Bearer <placeholder>` 替换为实际密钥
- Anthropic: 将 `X-Api-Key: <placeholder>` 替换为实际密钥

### 配置示例

`注意：以下网络端点地址 https://api.openai.com、http://192.168.1.100:9000 均为示例`

#### 配置文件yaml示例

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

边缘服务器可使用 `listen_host: "0.0.0.0"` 接收所有网络接口的连接。

#### 转发示例

```text
客户端请求:  POST http://127.0.0.1:8322/openai/v1/chat/completions -H "Authorization: Bearer sk_fake_key"
代理转发:    POST https://api.openai.com/v1/chat/completions       -H "Authorization: Bearer sk_sandbox_managed_openai_key"

客户端请求:  POST http://127.0.0.1:8322/custom/v1/chat/completions -H "Authorization: Bearer sk_fake_key"
代理转发:    POST http://192.168.1.100:9000/v1/chat/completions    -H "Authorization: Bearer sk_sandbox_managed_custom_key"
```

#### jiuwenclaw配置示例


| 配置项    | 旧值                          | 新值                             |
| --------- | ----------------------------- | -------------------------------- |
| api\_base | http://192.168.1.100:9000/v1/ | http://127.0.0.1:8322/custom/v1/ |
| api\_key  | sk_sandbox_managed_custom_key | sk_fake_key                      |

## 运行集成测试

`./tests/test.sh default` 会一次跑 `test_server_api_default.py` 和
`test_cli_default.py`，覆盖 server HTTP API 与 jiuwenbox CLI。通过
`--server-endpoint=URI` 切换连接方式，**传输协议自动从 URI 形式推断**：

```bash
# TCP (默认通路，等价于 --server-endpoint=http://127.0.0.1:8321)
./tests/test.sh default

# 自定义 TCP 监听 (host:port 会自动补 http:// 前缀)
./tests/test.sh default --server-endpoint=http://127.0.0.1:18321
./tests/test.sh default --server-endpoint=127.0.0.1:18321

# UDS 通路: 直接给 socket 文件的绝对路径
./tests/test.sh default --server-endpoint=unix:///tmp/jiuwenbox.sock
./tests/test.sh default --server-endpoint=unix:///tmp/jiuwenbox-sock/jiuwenbox.sock
```

注意 test.sh 本身**不会**起 server，请按选定通路先手工启动对应的 jiuwenbox
(TCP 走 `JIUWENBOX_LISTEN=tcp://0.0.0.0:8321` 或自定义端口，UDS 走
`JIUWENBOX_LISTEN=unix:///...`)。

运行指定测试用例：

```bash
python3 -m pytest tests/integration/test_server_api_default.py::TestPolicyEnforcement::test_network_mode_isolated_blocks_http_requests -s --server-endpoint 127.0.0.1:8321
```

### 性能测试

运行日常办公 workload 性能测试：

```bash
./tests/test.sh performance --server-endpoint 127.0.0.1:8321
```

可通过脚本参数设置沙箱数量、每个沙箱内的并发数，以及每个任务的循环次数：

```bash
./tests/test.sh performance \
  --sandbox-count 2 \
  --concurrency 16 \
  --loop 8 \
  --server-endpoint 127.0.0.1:8321
```

脚本会把这些参数映射为性能测试 fixture 使用的环境变量：

| 脚本参数 | 环境变量 | 默认值 |
| -------- | -------- | ------ |
| `--sandbox-count` | `JIUWENBOX_PERF_SANDBOX_COUNT` | `1` |
| `--concurrency` | `JIUWENBOX_PERF_CONCURRENCY` | `4` |
| `--loop` | `JIUWENBOX_PERF_LOOP` | `8` |

### 真实 LLM 集成测试

运行真实 LLM 集成测试需设置以下环境变量，若未设置环境变量，这些测试默认跳过：

```bash
export JIUWENBOX_TEST_LLM_ENDPOINT="https://api.openai.com"
export JIUWENBOX_TEST_LLM_API_KEY="sk_sandbox_managed_key"
export JIUWENBOX_TEST_LLM_MODEL="YOUR_MODEL"
```

## 注意事项

- 修改启动 policy 文件后，需要重启服务。
- 已存在的沙箱会继续使用创建时写入的 policy。
- `/exec` API 会把命令 stderr 作为命令执行结果返回；如果服务端诊断日志
  可能污染命令 stderr，应使用 debug 级别日志。

## CLI

`jiuwenbox` 提供单文件 Python CLI 客户端，包装
[`docs/jiuwenbox_server_api.md`](docs/jiuwenbox_server_api.md) 中所有 HTTP 接口。

`pip install` 之后会安装 `jiuwenbox` 可执行命令；源码内运行用
`python -m jiuwenbox.cli.jiuwenbox`。

```bash
# 健康检查
jiuwenbox health

# 沙箱生命周期
ID=$(jiuwenbox sandbox create --output plain)
jiuwenbox sandbox exec "$ID" -- python3 -c 'print("hi")'
jiuwenbox sandbox upload "$ID" ./data.csv /tmp/data.csv
jiuwenbox sandbox download "$ID" /tmp/result.json - | jq .
jiuwenbox sandbox ls --output table
jiuwenbox sandbox rm "$ID" --yes

# 沙箱策略
jiuwenbox policy get "$ID"

# 代理管理
jiuwenbox proxy create --prefix /openai --target https://api.openai.com --api-key sk-xxx
jiuwenbox proxy logs openai --lines 50
```

全局选项：

| 选项 | 默认值 | 环境变量 | 说明 |
| --- | --- | --- | --- |
| `--base-url` | `http://127.0.0.1:8321` | `JIUWENBOX_URL` | jiuwenbox 服务地址。接受 `http://host:port` 或 `unix:///abs/socket/path` |
| `--timeout` | `30` | `JIUWENBOX_TIMEOUT` | HTTP 超时秒数 |
| `--output / -o` | `json` | – | `json` \| `table` \| `plain` |
| `--verbose / -v` | 关闭 | – | stderr 打印 debug 日志 |
| `--no-color` | 关闭 | `NO_COLOR` | 关闭 stderr ANSI 颜色 |

退出码：`0` 成功 / `sandbox exec` 沙箱内退出码为 0；`1` HTTP 4xx/5xx；`2`
连接失败；`3` 本地参数 / 文件错误；`130` Ctrl+C。`sandbox exec` 子命令
会透传沙箱内进程的退出码。

## License

Apache-2.0
