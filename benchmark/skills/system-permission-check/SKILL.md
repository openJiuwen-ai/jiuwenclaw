---
name: system-permission-check
description: >-
  Check system resource permissions and capabilities: disk access, network access,
  process listing, CPU/memory info. Use when user asks about system capabilities,
  available resources, or needs to verify environment constraints before running tasks.
  NOT for modifying system settings or managing permissions.
allowed_tools: [bash]
---

# System Permission Check

检查系统环境的能力和资源权限。

## 执行方式

```bash
python scripts/check_perms.py [--check <category>] [--all]
```

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `--check` | 否 | 检查类别：`disk`、`network`、`process`、`cpu`、`memory` |
| `--all` | 否 | 检查所有类别 |

### 示例

```bash
# 检查所有系统权限
python scripts/check_perms.py --all

# 仅检查磁盘权限
python scripts/check_perms.py --check disk

# 检查网络和进程权限
python scripts/check_perms.py --check network --check process
```

## 检查项目

### 磁盘 (disk)
- 当前目录读写权限
- /tmp 目录读写权限
- 用户目录访问
- 磁盘空间信息

### 网络 (network)
- DNS 解析能力
- HTTP 外网连接
- 端口监听能力

### 进程 (process)
- 进程列表访问
- 子进程创建
- 信号发送权限

### CPU (cpu)
- CPU 核心数
- CPU 架构
- 负载信息

### 内存 (memory)
- 总内存/可用内存
- Swap 信息

## 输出格式

```
=== System Permission Check ===

[Disk] ✅ Read/Write access to current directory
[Disk] ✅ Read/Write access to /tmp
[Disk] ❌ No access to /root (Permission denied)

[Network] ✅ DNS resolution working
[Network] ✅ HTTP outbound connection OK
[Network] ❌ Port 80 bind failed (Permission denied)

[Process] ✅ Can list processes
[Process] ✅ Can create child processes

[CPU] ℹ️ 8 cores, x86_64, load avg: 1.2

[Memory] ℹ️ Total: 16GB, Available: 8.5GB

Summary: 8/10 checks passed, 2 failed
```

## 依赖

本脚本依赖 `psutil` 库获取系统和进程信息：

```bash
pip install psutil
```

如果 `psutil` 不可用，部分检查项会使用 `os` 模块的降级方案。

## 注意事项

- 在沙箱环境中，部分权限可能被限制（如端口绑定、进程列表）
- 权限检查结果是即时快照，不代表持续可用
- 某些检查需要短暂的网络连接（<1秒）
