# jiuwenclaw 日志采集脚本说明

本目录提供两套等价实现的打包工具，用于将本地 **jiuwenclaw 运行日志** 与 **可选 session 元数据** 打成单个 `tar.gz`，便于排查问题时发给研发或运维。**脚本不会上传任何内容**，仅在本地生成压缩包。

| 脚本 | 适用环境 |
|------|----------|
| `collect-jiuwenclaw-logs.sh` | Git Bash、WSL、Linux、macOS |
| `collect-jiuwenclaw-logs.ps1` | Windows PowerShell 5.1+（建议脚本保存为 **UTF-8 BOM**，便于中文帮助正确显示） |

---

## 采集内容与隐私白名单

为降低隐私泄露面，采集内容经过白名单过滤：

### 运行日志（`.logs`）

- 源路径：`{base}/.logs`
- **仅复制后缀为 `.log` 的文件**（匹配大小写不敏感，如 `app.LOG` 也会被纳入）。
- **保留相对路径**（子目录结构会反映到包内的 `runtime_logs/` 下）。
- 非 `.log` 文件、其它类型文件一律不打包。

### Session（`officeclaw_*`）

- 源路径：`{base}/{agent}/agent/sessions/`，子目录名形如 `officeclaw_{session_id}`。
- 每个被选中的 session **只在目录根下**复制以下文件（存在则拷）：
  - `history.json`
  - `metadata.json`
- 其它文件与子目录均不复制。若某 session 下两个文件都不存在，该 session **不会出现在包内**，也不计入「已纳入 session 数」。

### Session 排序与序号

- 所有 `officeclaw_*` 目录按 **创建时间** 从新到旧排序（脚本内说明为「新→旧」）。
- **序号从 1 开始**：**1 = 最新**，2 = 第二新，以此类推。
- 若文件系统无法提供可靠创建时间（部分 Linux/网络盘上 birth time 可能不可用），实现会 **退回使用修改时间** 排序；Windows 上通常以 **CreationTime** 为准。

---

## 默认数据路径（未指定 `--base` / `-Base` 时）

基目录 `{base}` 默认指向当前用户下的服务实例根目录：

| 平台 | 默认 `{base}` |
|------|----------------|
| Bash | `$HOME/.office-claw/.jiuwenclaw/service_default` |
| PowerShell | `%USERPROFILE%\.office-claw\.jiuwenclaw\service_default` |

可通过 `--service` / `-Service` 将最后的 `service_default` 换成其它服务名；或通过 `--base` / `-Base` 直接指定完整基目录。

在此基目录下，脚本会使用：

- `{base}/.logs` — 运行日志根目录（**必须存在且为目录**，否则脚本失败）。
- `{base}/{agent}/agent/sessions/` — session 根目录（默认 `{agent}` 为 `agent_default`）。

---

## 打包结果说明

### 压缩包文件名

`{prefix}_{YYYYMMDD_HHMMSS}.tar.gz`  

默认 `prefix` 为 `jiuwenclaw-logs`。文件名中**不包含计算机名/设备名**。

### 解压后目录结构

解压后得到**单一顶层文件夹**，名称与压缩包主文件名一致（无 `.tar.gz` 后缀），例如：

```text
jiuwenclaw-logs_20260506_143012/
  MANIFEST.txt           # 采集元数据（脚本版本、路径、session 列表与序号映射等）
  runtime_logs/          # 仅含 .logs 下 *.log 的镜像相对路径
  sessions/              # 仅当有至少一个 session 拷入 JSON 时存在
    officeclaw_<id>/
      history.json       # 若源目录存在
      metadata.json      # 若源目录存在
  SESSIONS_NOTE.txt      # 仅当本次未纳入任何 session 数据时出现，说明原因
```

---

## 命令行参数

两套脚本参数语义对齐（PowerShell 同时支持 `-Name` 与 `--name` 形式）。

| 参数 | 说明 |
|------|------|
| `--base` / `-Base` | 基目录（含 `.logs` 与 agent 那一层）。不设则用上文默认路径。 |
| `--service` / `-Service` | 与默认根拼接的服务目录名，默认 `service_default`。 |
| `--agent` / `-Agent` | agent 目录名，默认 `agent_default`。 |
| `--sessions` / `-Sessions` | Session 选择，默认 `1`（仅最新一个）。支持：正整数 `N`、`N-M`（闭区间且 **N≤M**，禁止 `3-1`）、`all`（全部 officeclaw，仍按新→旧编号）。 |
| `--output` / `-Output` | 压缩包输出目录，默认当前目录；不存在时会尝试创建（PowerShell 侧）。 |
| `--prefix` / `-Prefix` | 压缩包文件名前缀，默认 `jiuwenclaw-logs`。 |
| `--dry-run` / `-DryRun` | 只列出将要采集的路径，**不生成**压缩包。 |
| `-q` / `--quiet` / `-Quiet` | 减少过程输出。 |
| `-h` / `--help` / `-Help` | 显示帮助。 |

---

## 使用示例

在仓库中可先进入本目录：

```bash
cd scripts/collect_logs
```

### Bash（Git Bash / WSL / Linux / macOS）

```bash
# 查看帮助
./collect-jiuwenclaw-logs.sh --help

# 默认基目录 + 仅打包最新 session（默认 --sessions 1）
./collect-jiuwenclaw-logs.sh

# 指定基目录与最近 3 个 session（按序号 1～3）
./collect-jiuwenclaw-logs.sh --base "$HOME/.office-claw/.jiuwenclaw/service_default" --sessions 1-3

# 输出到 /tmp，仅预览
./collect-jiuwenclaw-logs.sh --dry-run --output /tmp
```

### PowerShell（Windows）

```powershell
cd scripts\collect_logs

# 查看帮助
.\collect-jiuwenclaw-logs.ps1 -Help

# 默认基目录 + 最新 session
.\collect-jiuwenclaw-logs.ps1

# 显式基目录与区间
.\collect-jiuwenclaw-logs.ps1 -Base "$env:USERPROFILE\.office-claw\.jiuwenclaw\service_default" -Sessions 1-3 -Output C:\Temp

# 预览
.\collect-jiuwenclaw-logs.ps1 --dry-run
```

若出现 **「无法加载文件……因为在此系统上禁止运行脚本」**，见下文常见问题第 5 条（执行策略）。

### 依赖说明

- **Bash 版**：需要 `bash`、`tar`、`cp`、`mkdir`、`rm`、`mktemp`、`find`、`sort`、`date`；排序用创建时间时会用到 `stat`（GNU/BSD）。
- **PowerShell 版**：需要 **PowerShell 5.1+**，以及系统自带的 **`tar.exe`**（Windows 10 及以后常见；或在 PATH 中可用）。

---

## 常见问题

1. **为何解压后没有某些日志文件？**  
   仅包含后缀为 `.log` 的文件；其它扩展名不会进入 `runtime_logs/`。

2. **为何选了 session 但包里没有 `sessions/`？**  
   对应目录下可能没有根级的 `history.json` / `metadata.json`，或序号超出当前存在的 session 数量。可查看包内 `SESSIONS_NOTE.txt` 或 `MANIFEST.txt`。

3. **PowerShell 报错与 `$HOME` / 中文乱码？**  
   脚本内使用 `$userProfileRoot` 等变量，勿使用 `$home` 赋值（会与只读变量 `$HOME` 冲突）。脚本建议以 **UTF-8 BOM** 保存。

4. **序号区间写反会怎样？**  
   例如 `--sessions 3-1` 会报错并退出；必须写成小号在前，如 `1-3`。

5. **PowerShell 提示「无法加载文件……禁止运行脚本」？**  
   这是 **执行策略** 限制（与脚本内容无关）。任选其一即可：

   - **推荐（当前用户长期生效）**：在 PowerShell 中执行  
     `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`  
     按提示确认后，再运行 `.\collect-jiuwenclaw-logs.ps1`。
   - **仅本次绕过**（不改系统策略）：  
     `powershell -ExecutionPolicy Bypass -File .\collect-jiuwenclaw-logs.ps1`  
     后面可照常跟 `-Help`、`--dry-run` 等参数。
   - **查看各作用域策略**：`Get-ExecutionPolicy -List`。

   企业环境若被组策略锁定，需联系管理员放行或使用 **Bypass** 方式在本机一次性执行。
