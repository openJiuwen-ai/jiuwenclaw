# jiuwenbox 服务接口说明

本文档基于当前 jiuwenbox 服务端源码整理，描述 HTTP 接口的用途、参数和响应格式。

## 基础信息

### 默认地址

```text
http://127.0.0.1:8321
```

业务接口统一使用 `/api/v1` 前缀，健康检查接口为 `/health`。

### 请求与响应约定

- JSON 请求使用 `Content-Type: application/json`
- 文件上传使用 `multipart/form-data`
- 文件下载返回 `application/octet-stream`
- 日志接口通常返回 `text/plain`

成功响应通常直接返回 JSON 对象或数组，例如：

```json
{
  "id": "abc123",
  "phase": "ready"
}
```

删除、上传等无响应体接口成功时返回 `204 No Content`。

错误响应通常为：

```json
{
  "error": "Sandbox 'abc123' not found"
}
```

部分代理接口使用 FastAPI 默认错误格式：

```json
{
  "detail": "Proxy 'openai' not found"
}
```

### 常见状态码

| 状态码 | 含义 |
| --- | --- |
| `200` | 请求成功 |
| `201` | 资源创建成功 |
| `204` | 请求成功，无响应体 |
| `400` | 请求参数错误或 policy 校验失败 |
| `404` | 沙箱、策略、文件、目录或代理不存在 |
| `409` | 当前状态不允许执行该操作 |
| `500` | 服务端内部错误 |

## 通用数据结构

### 沙箱引用信息

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 沙箱 ID |
| `phase` | string | 沙箱状态，取值为 `provisioning`、`ready`、`stopped`、`error`、`deleting` |
| `runtime` | string | 当前固定为 `process` |
| `pid` | integer/null | 沙箱生命周期进程 PID |
| `created_at` | string | 创建时间 |
| `started_at` | string/null | 启动时间 |
| `error_message` | string/null | 错误信息 |
| `env` | object | 创建沙箱时注入的环境变量 |

示例：

```json
{
  "id": "abc123def456",
  "phase": "ready",
  "runtime": "process",
  "pid": 12345,
  "created_at": "2026-04-25T11:30:00.000000",
  "started_at": "2026-04-25T11:30:01.000000+00:00",
  "error_message": null,
  "env": {
    "DEMO_KEY": "demo-value"
  }
}
```

### 命令执行结果

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `exit_code` | integer | 命令退出码 |
| `stdout` | string | 标准输出 |
| `stderr` | string | 标准错误 |

### 后台执行结果

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `started` | boolean | 后台进程是否创建成功 |
| `pid` | integer/null | supervisor 进程 PID |
| `command` | string[] | 实际执行的命令 |
| `error_message` | string/null | 创建失败原因 |

### 健康检查结果

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 固定为 `ok` |
| `version` | string | 服务版本 |
| `runtime` | string | 当前固定为 `process` |
| `landlock_supported` | boolean | 当前主机是否支持 Landlock |
| `sandboxes_active` | integer | 当前处于 `ready` 状态的沙箱数量 |

## 健康检查

### 健康检查接口

接口：`GET /health`

用途：检查服务是否存活，并返回运行时信息。

Python 请求示例：

```python
import requests

resp = requests.get("http://127.0.0.1:8321/health", timeout=30)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "status": "ok",
  "version": "0.1.0",
  "runtime": "process",
  "landlock_supported": true,
  "sandboxes_active": 1
}
```

## 沙箱接口

### 创建沙箱

接口：`POST /api/v1/sandboxes`

用途：创建沙箱。

请求字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `command` | string[] | 否 | 兼容字段，当前生命周期进程不会使用该命令 |
| `workdir` | string/null | 否 | 兼容字段 |
| `env` | object | 否 | 沙箱公共环境变量 |
| `policy` | object/null | 否 | 覆盖或追加的 policy 数据 |
| `policy_mode` | string | 否 | `override` 或 `append`，默认 `override` |

Python 请求示例：

```python
import requests

resp = requests.post(
    "http://127.0.0.1:8321/api/v1/sandboxes",
    json={
        "env": {
            "DEMO_KEY": "demo-value"
        },
        "policy_mode": "override"
    },
    timeout=30,
)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "id": "abc123def456",
  "phase": "ready",
  "runtime": "process",
  "pid": 12345,
  "created_at": "2026-04-25T11:30:00.000000",
  "started_at": "2026-04-25T11:30:01.000000+00:00",
  "error_message": null,
  "env": {
    "DEMO_KEY": "demo-value"
  }
}
```

### 查询沙箱列表

接口：`GET /api/v1/sandboxes`

用途：列出全部沙箱。

Python 请求示例：

```python
import requests

resp = requests.get("http://127.0.0.1:8321/api/v1/sandboxes", timeout=30)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
[
  {
    "id": "abc123def456",
    "phase": "ready",
    "runtime": "process",
    "pid": 12345,
    "created_at": "2026-04-25T11:30:00.000000",
    "started_at": "2026-04-25T11:30:01.000000+00:00",
    "error_message": null,
    "env": {}
  }
]
```

### 查询沙箱状态

接口：`GET /api/v1/sandboxes/{sandbox_id}`

用途：查询单个沙箱状态。

Python 请求示例：

```python
import requests

sandbox_id = "abc123def456"
resp = requests.get(f"http://127.0.0.1:8321/api/v1/sandboxes/{sandbox_id}", timeout=30)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "id": "abc123def456",
  "phase": "ready",
  "runtime": "process",
  "pid": 12345,
  "created_at": "2026-04-25T11:30:00.000000",
  "started_at": "2026-04-25T11:30:01.000000+00:00",
  "error_message": null,
  "env": {}
}
```

### 删除沙箱

接口：`DELETE /api/v1/sandboxes/{sandbox_id}`

用途：删除沙箱。

Python 请求示例：

```python
import requests

sandbox_id = "abc123def456"
resp = requests.delete(f"http://127.0.0.1:8321/api/v1/sandboxes/{sandbox_id}", timeout=30)
print(resp.status_code)
```

响应示例：

```text
204 No Content
```

### 启动沙箱

接口：`POST /api/v1/sandboxes/{sandbox_id}/start`

用途：启动已停止的沙箱。

Python 请求示例：

```python
import requests

sandbox_id = "abc123def456"
resp = requests.post(f"http://127.0.0.1:8321/api/v1/sandboxes/{sandbox_id}/start", timeout=30)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "id": "abc123def456",
  "phase": "ready",
  "runtime": "process",
  "pid": 12345,
  "created_at": "2026-04-25T11:30:00.000000",
  "started_at": "2026-04-25T11:31:00.000000+00:00",
  "error_message": null,
  "env": {}
}
```

### 停止沙箱

接口：`POST /api/v1/sandboxes/{sandbox_id}/stop`

用途：停止沙箱。

Python 请求示例：

```python
import requests

sandbox_id = "abc123def456"
resp = requests.post(f"http://127.0.0.1:8321/api/v1/sandboxes/{sandbox_id}/stop", timeout=30)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "id": "abc123def456",
  "phase": "stopped",
  "runtime": "process",
  "pid": null,
  "created_at": "2026-04-25T11:30:00.000000",
  "started_at": "2026-04-25T11:31:00.000000+00:00",
  "error_message": null,
  "env": {}
}
```

### 重启沙箱

接口：`POST /api/v1/sandboxes/{sandbox_id}/restart`

用途：重启沙箱。

Python 请求示例：

```python
import requests

sandbox_id = "abc123def456"
resp = requests.post(f"http://127.0.0.1:8321/api/v1/sandboxes/{sandbox_id}/restart", timeout=30)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "id": "abc123def456",
  "phase": "ready",
  "runtime": "process",
  "pid": 22345,
  "created_at": "2026-04-25T11:30:00.000000",
  "started_at": "2026-04-25T11:32:00.000000+00:00",
  "error_message": null,
  "env": {}
}
```

### 同步执行命令

接口：`POST /api/v1/sandboxes/{sandbox_id}/exec`

用途：在沙箱内同步执行命令，等待命令结束后返回结果。

请求字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `command` | string[] | 是 | 待执行命令 |
| `workdir` | string/null | 否 | 命令工作目录 |
| `env` | object/null | 否 | 本次执行追加环境变量 |
| `stdin` | string/null | 否 | 标准输入文本 |
| `timeout_seconds` | integer/null | 否 | 超时时间，单位秒 |

Python 请求示例：

```python
import requests

sandbox_id = "abc123def456"
resp = requests.post(
    f"http://127.0.0.1:8321/api/v1/sandboxes/{sandbox_id}/exec",
    json={
        "command": ["python3", "-c", "print('hello')"],
        "timeout_seconds": 10
    },
    timeout=30,
)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "exit_code": 0,
  "stdout": "hello\n",
  "stderr": ""
}
```

### 启动后台命令

接口：`POST /api/v1/sandboxes/{sandbox_id}/exec_background`

用途：在沙箱内启动后台进程，进程创建后立即返回。

请求字段与 `/exec` 相同。

Python 请求示例：

```python
import requests

sandbox_id = "abc123def456"
resp = requests.post(
    f"http://127.0.0.1:8321/api/v1/sandboxes/{sandbox_id}/exec_background",
    json={
        "command": ["python3", "-m", "http.server", "18080"]
    },
    timeout=30,
)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "started": true,
  "pid": 23456,
  "command": ["python3", "-m", "http.server", "18080"],
  "error_message": null
}
```

### 查看沙箱日志

接口：`GET /api/v1/sandboxes/{sandbox_id}/logs`

用途：读取沙箱审计日志。

Python 请求示例：

```python
import requests

sandbox_id = "abc123def456"
resp = requests.get(f"http://127.0.0.1:8321/api/v1/sandboxes/{sandbox_id}/logs", timeout=30)
print(resp.status_code)
print(resp.text)
```

响应示例：

```text
[2026-04-25T11:30:00.000000] sandbox_created
[2026-04-25T11:30:01.000000] sandbox_started
```

### 上传文件

接口：`POST /api/v1/sandboxes/{sandbox_id}/upload`

用途：向沙箱内上传文件。

查询参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `sandbox_path` | string | 是 | 沙箱内目标路径 |

Python 请求示例：

```python
import requests

sandbox_id = "abc123def456"
with open("local.txt", "rb") as f:
    resp = requests.post(
        f"http://127.0.0.1:8321/api/v1/sandboxes/{sandbox_id}/upload",
        params={"sandbox_path": "/tmp/remote.txt"},
        files={"file": ("local.txt", f)},
        timeout=30,
    )
print(resp.status_code)
```

响应示例：

```text
204 No Content
```

### 下载文件

接口：`GET /api/v1/sandboxes/{sandbox_id}/download`

用途：从沙箱内下载文件。

查询参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `sandbox_path` | string | 是 | 沙箱内源文件路径 |

Python 请求示例：

```python
import requests

sandbox_id = "abc123def456"
resp = requests.get(
    f"http://127.0.0.1:8321/api/v1/sandboxes/{sandbox_id}/download",
    params={"sandbox_path": "/tmp/remote.txt"},
    timeout=30,
)
print(resp.status_code)
print(resp.content)
```

响应示例：

```text
二进制文件内容
```

### 列出文件

接口：`GET /api/v1/sandboxes/{sandbox_id}/files`

用途：列出沙箱目录下的文件和目录。

查询参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `sandbox_path` | string | 是 | 待列举目录 |
| `recursive` | boolean | 否 | 是否递归 |
| `max_depth` | integer/null | 否 | 递归深度限制 |
| `include_files` | boolean | 否 | 是否包含文件，默认 `true` |
| `include_dirs` | boolean | 否 | 是否包含目录，默认 `true` |

Python 请求示例：

```python
import requests

sandbox_id = "abc123def456"
resp = requests.get(
    f"http://127.0.0.1:8321/api/v1/sandboxes/{sandbox_id}/files",
    params={
        "sandbox_path": "/tmp",
        "recursive": "true",
        "max_depth": 2,
        "include_files": "true",
        "include_dirs": "true",
    },
    timeout=30,
)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "items": [
    {
      "name": "remote.txt",
      "path": "/tmp/remote.txt",
      "size": 12,
      "is_directory": false,
      "modified_time": "2026-04-25T11:35:00.000000",
      "type": ".txt"
    }
  ]
}
```

### 搜索文件

接口：`GET /api/v1/sandboxes/{sandbox_id}/search`

用途：按 glob 模式搜索文件。

查询参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `sandbox_path` | string | 是 | 搜索根目录 |
| `pattern` | string | 是 | 匹配模式 |
| `exclude_patterns` | string[] | 否 | 排除模式，可重复传入 |

Python 请求示例：

```python
import requests

sandbox_id = "abc123def456"
resp = requests.get(
    f"http://127.0.0.1:8321/api/v1/sandboxes/{sandbox_id}/search",
    params={
        "sandbox_path": "/tmp",
        "pattern": "*.txt",
        "exclude_patterns": "*.bak",
    },
    timeout=30,
)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "items": [
    {
      "name": "remote.txt",
      "path": "/tmp/remote.txt",
      "size": 12,
      "is_directory": false,
      "modified_time": "2026-04-25T11:35:00.000000",
      "type": ".txt"
    }
  ]
}
```

## Policy 接口

### 查询沙箱策略

接口：`GET /api/v1/policies/{sandbox_id}`

用途：获取某个沙箱当前生效的 policy。

Python 请求示例：

```python
import requests

sandbox_id = "abc123def456"
resp = requests.get(f"http://127.0.0.1:8321/api/v1/policies/{sandbox_id}", timeout=30)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "name": "default-policy",
  "process": {
    "run_as_user": "nobody",
    "run_as_group": "nobody"
  },
  "network": {
    "mode": "host"
  }
}
```

## 代理接口

代理接口用于管理 inference privacy proxy。

### 创建代理

接口：`POST /api/v1/proxies`

用途：创建一个代理路由。

请求字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `path_prefix` | string | 是 | 路由前缀，如 `/openai` |
| `target_endpoint` | string | 是 | 目标服务地址 |
| `api_key` | string | 否 | 注入到上游的 API Key |
| `skip_cert_verify` | boolean | 否 | 是否跳过证书校验 |

Python 请求示例：

```python
import requests

resp = requests.post(
    "http://127.0.0.1:8321/api/v1/proxies",
    json={
        "path_prefix": "/openai",
        "target_endpoint": "https://api.openai.com",
        "api_key": "sk-demo",
        "skip_cert_verify": False
    },
    timeout=30,
)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "name": "openai",
  "state": "stopped",
  "created_at": "2026-04-25T11:40:00.000000"
}
```

### 查询代理列表

接口：`GET /api/v1/proxies`

用途：列出全部代理路由。

Python 请求示例：

```python
import requests

resp = requests.get("http://127.0.0.1:8321/api/v1/proxies", timeout=30)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
[
  {
    "name": "openai",
    "state": "running",
    "listen_port": 18080,
    "route": {
      "path_prefix": "/openai",
      "target_endpoint": "https://api.openai.com",
      "api_key": "sk-demo..."
    },
    "created_at": "2026-04-25T11:40:00.000000",
    "started_at": "2026-04-25T11:41:00.000000",
    "error_message": null
  }
]
```

### 查询代理详情

接口：`GET /api/v1/proxies/{proxy_name}`

用途：查询单个代理路由详情。

Python 请求示例：

```python
import requests

proxy_name = "openai"
resp = requests.get(f"http://127.0.0.1:8321/api/v1/proxies/{proxy_name}", timeout=30)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "name": "openai",
  "state": "running",
  "listen_port": 18080,
  "route": {
    "path_prefix": "/openai",
    "target_endpoint": "https://api.openai.com",
    "api_key": "sk-demo",
    "skip_cert_verify": false,
    "target_host": "api.openai.com",
    "target_port": 443,
    "use_tls": true
  },
  "created_at": "2026-04-25T11:40:00.000000",
  "started_at": "2026-04-25T11:41:00.000000",
  "error_message": null
}
```

### 删除代理

接口：`DELETE /api/v1/proxies/{proxy_name}`

用途：删除代理路由。

Python 请求示例：

```python
import requests

proxy_name = "openai"
resp = requests.delete(f"http://127.0.0.1:8321/api/v1/proxies/{proxy_name}", timeout=30)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "name": "openai",
  "deleted": true
}
```

### 启动代理

接口：`POST /api/v1/proxies/{proxy_name}/start`

用途：启动代理路由。

Python 请求示例：

```python
import requests

proxy_name = "openai"
resp = requests.post(f"http://127.0.0.1:8321/api/v1/proxies/{proxy_name}/start", timeout=30)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "name": "openai",
  "state": "running",
  "started_at": "2026-04-25T11:41:00.000000",
  "error_message": null
}
```

### 停止代理

接口：`POST /api/v1/proxies/{proxy_name}/stop`

用途：停止代理路由。

Python 请求示例：

```python
import requests

proxy_name = "openai"
resp = requests.post(f"http://127.0.0.1:8321/api/v1/proxies/{proxy_name}/stop", timeout=30)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "name": "openai",
  "state": "stopped",
  "error_message": null
}
```

### 更新代理

接口：`PUT /api/v1/proxies/{proxy_name}`

用途：更新代理路由目标。

请求字段与创建代理相同，但 `path_prefix` 仅用于构造请求体，服务端会保留原有路由名对应的前缀。

Python 请求示例：

```python
import requests

proxy_name = "openai"
resp = requests.put(
    f"http://127.0.0.1:8321/api/v1/proxies/{proxy_name}",
    json={
        "path_prefix": "/openai",
        "target_endpoint": "https://api.openai.com/v1",
        "api_key": "sk-demo-new",
        "skip_cert_verify": False
    },
    timeout=30,
)
print(resp.status_code)
print(resp.json())
```

响应示例：

```json
{
  "name": "openai",
  "state": "running",
  "started_at": "2026-04-25T11:42:30.000000",
  "error_message": null
}
```

### 查看代理日志

接口：`GET /api/v1/proxies/{proxy_name}/logs`

用途：查看代理日志。

查询参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `lines` | integer/null | 否 | 只返回最后 N 行 |

Python 请求示例：

```python
import requests

proxy_name = "openai"
resp = requests.get(
    f"http://127.0.0.1:8321/api/v1/proxies/{proxy_name}/logs",
    params={"lines": 50},
    timeout=30,
)
print(resp.status_code)
print(resp.text)
```

响应示例：

```text
[2026-04-25T11:41:00.000000] Global proxy started on port 18080
[2026-04-25T11:41:00.100000] Route 'openai' enabled for routing
```

## 说明

- `sandbox.create` 仍接受 `command` 和 `workdir` 字段，但当前生命周期持有进程为服务内部 daemon，这两个字段不会出现在 `SandboxRef` 返回体中。
- `sandbox.exec` 和 `sandbox.exec_background` 中的 `workdir`、`env`、`timeout_seconds` 仍然有效。
- 文档示例中的时间、PID、ID 仅为示意。
