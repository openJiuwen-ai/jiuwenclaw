# JiuwenBox Proxy HTTP Basic 认证 — 部署与使用指南

> 适用范围：JiuwenBox 沙箱内脚本经 JiuwenBox HTTP Proxy 访问 Neo4j / MindsDB 等 HTTP 服务时，由 Proxy 按路由主动注入 `Authorization: Basic base64(username:password)`，沙箱脚本无需携带真实凭据。
>
> **仅支持 HTTP Basic**。不支持 Neo4j Bolt（7687）、HTTP Digest、OAuth。

## 1. 安全模型要点

- 真实密码**只存在于服务端**：内联 `password`（明文，仅开发测试）或服务端可读的 `password_file`（生产推荐）。
- `password_file` 由 JiuwenBox 服务进程在**装配路由时**一次性读入内存；运行时转发不再触发文件 IO；CLI **不读取**该文件。
- 任何接口（列表 / 详情 / 日志 / 错误响应）都**不返回明文密码**，仅返回 `username` / `password_configured` / `password_file`。
- `api_key` 与 `basic_auth` 互斥；`password` 与 `password_file` 二选一。
- 客户端任何 `Authorization` 头（Bearer / Basic / 其他，大小写不敏感）都会被覆盖为唯一的 `Authorization: Basic ...`；`X-Api-Key` 不受影响。

## 2. 配置示例

### 2.1 YAML 内联密码（仅开发测试）

```yaml
inference_privacy_proxies:
  listen_host: "127.0.0.1"
  listen_port: 8322
  routes:
    - path_prefix: "neo4j"
      target_endpoint: "http://neo4j.internal:7474"
      basic_auth:
        username: "neo4j"
        # 警告：内联 password 会以明文写入 YAML 并随策略落盘。
        # 仅用于开发/测试环境，生产环境请使用 password_file。
        password: "dev-only-password"
```

### 2.2 Linux `0600` Secret 文件

```bash
# 1) 在服务端创建仅属主可读的密码文件（推荐 0600；0640/0644 会被接受但记告警）
install -m 0600 /dev/stdin /etc/jiuwenbox/secrets/neo4j_password <<'EOF'
replace-with-real-password
EOF
chown root:root /etc/jiuwenbox/secrets/neo4j_password
```

```yaml
inference_privacy_proxies:
  listen_port: 8322
  listen_host: "127.0.0.1"
  routes:
    - path_prefix: "neo4j"
      target_endpoint: "http://neo4j.internal:7474"
      basic_auth:
        username: "neo4j"
        password_file: "/etc/jiuwenbox/secrets/neo4j_password"
```

> 读取时仅去除结尾的一个换行；密码内部的空格 / 冒号等字符保留。CR / LF / NUL / 控制字符会被拒绝。

### 2.3 Docker Secret（或只读文件挂载）

```bash
# 创建 Docker Secret
printf '%s' "replace-with-real-password" | docker secret create neo4j_password -
```

```yaml
# docker-compose.yml 片段
services:
  jiuwenbox:
    image: jiuwenbox:latest
    secrets:
      - neo4j_password
    environment:
      JIUWENBOX_POLICY_PATH: /etc/jiuwenbox/policy.yaml
    volumes:
      - ./policy.yaml:/etc/jiuwenbox/policy.yaml:ro
secrets:
  neo4j_password:
    external: true
```

```yaml
# policy.yaml 中引用 Secret 在容器内的挂载路径（默认 0444，服务端仍可读）
inference_privacy_proxies:
  listen_port: 8322
  listen_host: "0.0.0.0"
  routes:
    - path_prefix: "neo4j"
      target_endpoint: "http://neo4j.internal:7474"
      basic_auth:
        username: "neo4j"
        password_file: "/run/secrets/neo4j_password"
```

### 2.4 Kubernetes Secret 文件挂载

```yaml
# 1) Secret（base64 编码占位，勿使用真实环境密码）
apiVersion: v1
kind: Secret
metadata:
  name: neo4j-basic-auth
type: Opaque
stringData:
  password: "replace-with-real-password"
---
# 2) Deployment 将 Secret 以只读文件挂载
apiVersion: apps/v1
kind: Deployment
metadata:
  name: jiuwenbox
spec:
  template:
    spec:
      containers:
        - name: jiuwenbox
          image: jiuwenbox:latest
          env:
            - name: JIUWENBOX_POLICY_PATH
              value: /etc/jiuwenbox/policy.yaml
          volumeMounts:
            - name: policy
              mountPath: /etc/jiuwenbox/policy.yaml
              readOnly: true
            - name: neo4j-secret
              mountPath: /run/secrets
              readOnly: true
      volumes:
        - name: policy
          configMap: { name: jiuwenbox-policy }
        - name: neo4j-secret
          secret:
            secretName: neo4j-basic-auth
            defaultMode: 0400
```

```yaml
# policy.yaml 引用
inference_privacy_proxies:
  listen_port: 8322
  listen_host: "0.0.0.0"
  routes:
    - path_prefix: "neo4j"
      target_endpoint: "http://neo4j.internal:7474"
      basic_auth:
        username: "neo4j"
        password_file: "/run/secrets/password"
```

> K8s Secret 默认 `0644`；JiuwenBox 接受但会记告警，建议通过 `defaultMode: 0400` 收紧。

## 3. REST API 创建 / 更新 Basic 路由

```bash
# 创建：密码来自服务端 Secret 文件
curl -X POST http://127.0.0.1:8321/api/v1/proxies \
  -H 'Content-Type: application/json' \
  -d '{
        "path_prefix": "/neo4j",
        "target_endpoint": "http://neo4j.internal:7474",
        "basic_auth": {"username": "neo4j", "password_file": "/run/secrets/neo4j_password"}
      }'

# 更新（全量替换：省略 basic_auth 即清空 Basic，与省略 api_key 清空 api_key 一致）
curl -X PUT http://127.0.0.1:8321/api/v1/proxies/neo4j \
  -H 'Content-Type: application/json' \
  -d '{
        "path_prefix": "/neo4j",
        "target_endpoint": "http://neo4j.internal:7474",
        "basic_auth": {"username": "neo4j", "password_file": "/run/secrets/neo4j_password"}
      }'

# 列表 / 详情：响应中 basic_auth 仅含 username / password_configured / password_file，绝不含 password
curl http://127.0.0.1:8321/api/v1/proxies
curl http://127.0.0.1:8321/api/v1/proxies/neo4j
```

## 4. CLI 创建 / 更新 Basic 路由

```bash
# 4.1 --password-file（生产推荐；CLI 不读取文件，由服务端读取）
jiuwenbox proxy create \
  --prefix /neo4j --target http://neo4j.internal:7474 \
  --username neo4j --password-file /run/secrets/neo4j_password

# 4.2 --password-stdin（密码走标准输入，不出现在进程参数中）
printf '%s' "$NEO4J_PASSWORD" | jiuwenbox proxy create \
  --prefix /neo4j --target http://neo4j.internal:7474 \
  --username neo4j --password-stdin

# 4.3 --password（仅开发测试；可能暴露在 shell history / 进程参数中）
jiuwenbox proxy create \
  --prefix /neo4j --target http://neo4j.internal:7474 \
  --username neo4j --password dev-only-password

# 更新（同样支持三个密码来源，三选一；与 --api-key 互斥）
jiuwenbox proxy update neo4j \
  --prefix /neo4j --target http://neo4j.internal:7474 \
  --username neo4j --password-file /run/secrets/neo4j_password

# 查看脱敏详情
jiuwenbox proxy get neo4j
```

规则：`--password` / `--password-file` / `--password-stdin` 三选一；使用任一 Basic 选项时 `--username` 必填；`--api-key` 与 Basic 选项互斥。CLI 列表 / 详情 / 错误输出均不显示明文密码。

## 5. 沙箱内脚本调用 Neo4j HTTP 接口

沙箱脚本**不携带任何凭据**，直接请求 Proxy；Proxy 注入 Basic 头后转发到上游 Neo4j。Neo4j HTTP 查询接口（`POST /db/{database}/query/v2` 或旧版 `POST /db/data/cypher`，以实际 Neo4j 版本为准），**不使用 Bolt 协议**。

```python
# sandbox script: query_neo4j.py（无任何用户名/密码/Authorization）
import json, urllib.request

url = "http://127.0.0.1:8322/neo4j/db/neo4j/query/v2"  # 经 JiuwenBox Proxy
payload = json.dumps({"statement": "RETURN 1 AS value"}).encode()
req = urllib.request.Request(
    url, data=payload, method="POST",
    headers={"Content-Type": "application/json", "Accept": "application/json"},
)
# 注意：不要设置 Authorization —— Proxy 会注入正确的 Basic 头并覆盖任意客户端 Authorization。
with urllib.request.urlopen(req, timeout=10) as resp:
    print(resp.status, resp.read().decode())
```

等价 `curl`（沙箱内）：

```bash
curl -s -X POST http://127.0.0.1:8322/neo4j/db/neo4j/query/v2 \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{"statement":"RETURN 1 AS value"}'
```

## 6. 不支持

- **Neo4j Bolt 协议（7687）**：本 Proxy 仅代理 HTTP/HTTPS；Bolt 不走本 Proxy。
- **HTTP Digest 认证**：仅支持 HTTP Basic。
- **OAuth / Bearer 令牌上游注入到 Basic 路由**：Basic 路由会覆盖客户端 `Authorization`；如需 Bearer 上游认证请使用 `api_key` 路由。

## 7. 脱敏不变量

- 列表 / 详情：`basic_auth` 仅含 `username` / `password_configured` / `password_file`。
- 日志：仅记录 `Injected Basic auth for route '<prefix>'`，不含 base64 凭据。
- 错误响应：装配失败返回通用文案（如 `password_file not found or not a regular file`），不含密码。
- 进程参数：生产应使用 `--password-file` 或 `--password-stdin`，避免密码出现在 `ps` / shell history。
