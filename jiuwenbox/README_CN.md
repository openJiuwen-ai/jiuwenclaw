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

## 安装

```bash
cd jiuwenclaw/jiuwenbox
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip build
python3 -m build --wheel
python3 -m pip install dist/jiuwenbox-*.whl
```

## 启动服务

### 本地启动

设置默认 policy 路径，并通过 `python -m` 启动已安装的服务：

```bash
export JIUWENBOX_POLICY_PATH="$(pwd)/configs/default-policy.yaml"
sudo -E .venv/bin/python -m uvicorn jiuwenbox.server.app:app --host 0.0.0.0 --port 8321 --log-level debug
```

如需使用其他 policy 或端口，可修改环境变量或 uvicorn 参数：

```bash
export JIUWENBOX_POLICY_PATH="$(pwd)/configs/jiuwenclaw-policy.yaml"
sudo -E .venv/bin/python -m uvicorn jiuwenbox.server.app:app --host 0.0.0.0 --port 9000 --log-level debug
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

运行指定 policy 对应的集成测试：

```bash
./tests/test.sh default # jiuwenbox 使用 default-policy.yaml 作为安全策略运行服务。
./tests/test.sh yuanrong # jiuwenbox 使用 yuanrong.yaml 运行服务，代理监听端口 8322。
```

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

## License

Apache-2.0
