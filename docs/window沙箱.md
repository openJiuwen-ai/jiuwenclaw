- # Windows 沙箱技术分析 & JiuwenBox 移植方案

> 分析 Codex 与 Claude Code (sandbox-runtime) 的 Windows 沙箱实现，并给出 JiuwenBox 的 Windows 沙箱移植方案。

---

## 一、背景

**JiuwenBox 的现状**：

- JiuwenBox 是一个基于 Linux 的轻量级沙箱服务，核心依赖 `bubblewrap` + Linux 命名空间实现进程隔离
- 架构：Python FastAPI 服务 → 每个沙箱一个长寿命 bubblewrap daemon 进程 → 通过 Unix Domain Socket IPC 收发 exec 请求 → daemon fork+exec 子命令
- 隔离机制包括：bubblewrap（进程/挂载隔离）、Landlock（文件系统）、seccomp（系统调用）、network namespace（网络）、cgroup（资源限制）
- supervisor/ 目录下包含 `bwrap.py`、`landlock.py`、`seccomp.py`、`network.py`、`cgroup.py`、`daemon_ipc.py`、`sandbox_daemon.py` 等模块
- **没有 Windows 支持** —— 所有隔离机制都是 Linux 专属

**目标**：研究 Codex 和 Claude Code (sandbox-runtime) 的 Windows 沙箱方案，将等效能力移植到 JiuwenBox，使其能在 Windows 上运行。**移植不是替代——Linux 路径保持不变，仅在 `ProcessRuntime` 层新增 Windows 分支。**

---

## 二、Windows 安全机制基础

本章介绍后续分析中反复出现的 Windows 安全概念。如果你已经熟悉 Windows 安全体系，可跳过本章直接阅读第三章。

### 2.1 进程 Token（Process Token）

**Token 是 Windows 中"你是谁、你能做什么"的唯一凭证。**

每当一个进程被创建，Windows 内核为它分配一个 Token 对象，包含：

  ```
Token
├── User SID            ← 你是谁（S-1-5-21-xxx-1001）
├── Group SIDs[]        ← 你属于哪些组（Administrators, Users, ...）
├── Privileges[]        ← 你有哪些特权（SeShutdownPrivilege, ...）
├── Integrity Level     ← 你的可信等级（Low/Medium/High/System）
├── Logon Session SID   ← 本次登录会话的唯一 ID
├── Restricted SIDs[]   ← ★ 受限 SID 列表（Write-Restricted Token 的核心）
└── Default DACL        ← 创建新对象时的默认权限
  ```

**关键特性**：

- Token 在进程创建时确定，进程运行期间不可修改
- 子进程**默认继承**父进程的 Token（除非显式创建新 Token）
- Token 是内核对象，用户态无法伪造

**与 Linux 的对比**：

| Linux                        | Windows                                             |
| ---------------------------- | --------------------------------------------------- |
| UID/GID（数值）              | User SID（字符串，如 `S-1-5-21-xxx-1001`）          |
| Supplementary Groups         | Group SIDs                                          |
| Capabilities (`CAP_NET_RAW`) | Privileges (`SeShutdownPrivilege`)                  |
| `clone()` with new namespace | `CreateRestrictedToken()` + `CreateProcessAsUser()` |
| `setuid()` / `setgid()`      | 无法在运行时切换（Token 不可变，只能 spawn 新进程） |

### 2.2 SID（Security Identifier）

**SID 是 Windows 中所有安全主体的唯一标识符。** 每个用户、组、登录会话、甚至自定义权限标记都有一个 SID。

#### SID 格式

  ```
S-1-5-21-3623811015-3361044348-30300820-1001
│ │ │  └─────────────────┬─────────────────┘  └─ RID (Relative ID)
│ │ │            Domain/机器标识符              具体的用户或组
│ │ └─ NT Authority
│ └─ 版本号
└─ "SID" 前缀
  ```

#### 常见 SID

| SID            | 含义                               |
| -------------- | ---------------------------------- |
| `S-1-1-0`      | **Everyone** —— 所有用户，含匿名   |
| `S-1-5-32-544` | Administrators 组                  |
| `S-1-5-32-545` | Users 组                           |
| `S-1-5-18`     | LOCAL SYSTEM（最高权限）           |
| `S-1-5-5-X-Y`  | Logon Session —— 本次登录的会话 ID |

#### 合成 SID（Synthetic SID）

这是沙箱设计中的**核心技巧**：Windows 允许创建**不与任何真实用户关联的 SID**，这些 SID 可以出现在 ACL 中但不对应任何账户。

  ```powershell
# 创建一个合成 SID（PowerShell 无法直接创建，需用 C/Rust 调用 Win32 API）
# 伪代码:
sid = CreateWellKnownSid(WinWorldSid)  # 创建 Everyone SID
authz_sid = ConvertStringSidToSid("S-1-5-21-xxx-custom")
  ```

**为什么有用**：给这个合成 SID 授予特定目录的写权限，然后要求沙箱进程的 Token 中携带这个 SID。即使进程以真实用户身份运行，只要 Token 里没有这个合成 SID，就写不了。

这类似于 Linux 的 group-based access —— 但更灵活，因为合成 SID 不影响系统中其他任何进程。

### 2.3 ACL 与 ACE（Access Control List / Entry）

**ACL 是 Windows 文件系统中"谁能做什么"的规则列表。**

#### 结构

  ```
每个文件/目录都有一个 Security Descriptor（安全描述符）：

Security Descriptor
├── Owner SID              ← 文件所有者
├── Group SID              ← 文件所属组
├── DACL (Discretionary ACL)
│   ├── ACE: Allow  Read   S-1-5-21-xxx-1001  ← 允许某用户读
│   ├── ACE: Allow  Write  S-1-5-21-xxx-1001  ← 允许某用户写
│   ├── ACE: Deny   Write  S-1-5-21-xxx-custom ← ★ 拒绝合成 SID 写
│   └── ACE: Allow  Write  S-1-5-21-xxx-custom ← ★ 允许合成 SID 写
└── SACL (System ACL)      ← 审计日志用
  ```

#### ACE 的优先级规则（沙箱设计的关键）

Windows ACL 评估遵循严格的规则：

  ```
1. 显式 DENY 优先于显式 ALLOW
2. 继承的 DENY 优先于继承的 ALLOW
3. 显式优先于继承
4. 父目录的 ALLOW 可被子目录继承

★ 对于 Write-Restricted Token:
   所有上述检查通过后，还要再检查 Restricted SID 列表。
  ```

#### 与 Linux 文件权限的对比

| Linux                   | Windows                                                      |
| ----------------------- | ------------------------------------------------------------ |
| `chmod 755` (rwxr-xr-x) | ACL 中的 Allow/Deny ACE                                      |
| `chown user:group`      | Owner SID + Group SID                                        |
| `setfacl -m u:user:rw`  | 添加 Allow ACE for specific SID                              |
| 仅支持 r/w/x 三种权限   | 支持 Read/Write/Execute/Delete/ChangePermissions/TakeOwnership 等细粒度权限 |

### 2.4 Restricted Token 与 Write-Restricted Token

**这是 Codex Windows 沙箱最核心的安全原语，也是理解本文的关键。**

#### 概念

Restricted Token 是 Windows 提供的一种**从现有 Token 派生出受限版本**的机制。它通过 `CreateRestrictedToken()` API 创建。

  ```
原始 Token                           Restricted Token
├── User: 牛马 (S-1-5-21-xxx-1001)  ├── User: 牛马 (S-1-5-21-xxx-1001) ← 身份不变
├── Groups: [Admin, Users, ...]     ├── Groups: [Users, ...] ← Admin 被移除
├── Privileges: [SeShutdown, ...]   ├── Privileges: [] ← 全部清除
└── Restricted SIDs: []             └── ★ Restricted SIDs: [Everyone, LogonSession, JHXSandboxWrite]
  ```

#### Write-Restricted Token 的特殊行为

**普通 Restricted Token**：做任何操作都需要 double-check。
**Write-Restricted Token**（`WRITE_RESTRICTED` flag）：只对**写操作**做 double-check。

  ```
进程尝试读取 C:\Windows\System32\notepad.exe：
  → 检查 User SID (牛马) + Group SIDs (Users) → Users 有 Read 权限 → ✓ 通过
  → 不需要检查 Restricted SIDs（只有写操作才触发）

进程尝试写入 C:\Users\牛马\Documents\secret.docx：
  → 检查 User SID (牛马) → 牛马有 Write 权限 → ✓ 第一关通过
  → ★ 检查 Restricted SIDs → [Everyone, LogonSession, JHXSandboxWrite]
     → Everyone 在文件 ACL 中没有显式的写权限？→ ✗ 第二关失败！
     → JHXSandboxWrite 在文件 ACL 中有写权限？→ 没有 → ✗ 第二关失败！
  → 结果：写入被拒绝！

进程尝试写入 C:\workspace\file.txt（JHXSandboxWrite 在 ACL 中有 Allow Write）：
  → 检查 User SID (牛马) → 牛马有 Write 权限 → ✓ 第一关通过
  → ★ 检查 Restricted SIDs → ... JHXSandboxWrite
     → JHXSandboxWrite 在 ACL 中有 Allow Write？→ ✓ 有！
  → 结果：写入成功！
  ```

#### 为什么这么设计

Write-Restricted Token 的巧妙之处在于 **"读操作不受影响，写操作受双重控制"**：

- Agent 需要读很多文件（系统 DLL、配置文件、工具链），这些不需要审批
- Agent 的写操作才是危险操作，需要精确控制
- 通过 ACL 控制合成 SID 的写权限，就能精确控制"沙箱进程可以在哪里写"

> **用 Linux 的话说**：这就像 `chroot` 只能限制文件系统，而 Write-Restricted Token 是"只限制 write 系统调用的 ACL 机制"。

### 2.5 CreateProcessAsUserW 与 CreateProcessWithLogonW

**Windows 创建进程的 API 有以下几种**，理解它们的区别对于沙箱架构至关重要：

| API                       | 用法                            | 提权需求                                 | 限制                              |
| ------------------------- | ------------------------------- | ---------------------------------------- | --------------------------------- |
| `CreateProcessW`          | 以当前用户启动进程              | 无需                                     | 不能切换用户                      |
| `CreateProcessAsUserW`    | 以指定 Token 启动进程           | **需要 `SeAssignPrimaryTokenPrivilege`** | 这是两个沙箱方案都撞上的权限墙    |
| `CreateProcessWithLogonW` | 以指定用户名/密码登录并启动进程 | 仅需知道凭据                             | 不能传 Restricted Token           |
| `CreateProcessWithTokenW` | 以指定 Token 启动进程           | 需要 `SeImpersonatePrivilege`            | 要求 token 是 impersonation token |

#### 为什么两个沙箱都用两跳启动

直接路径：

  ```
broker.exe (真实用户)
  → LogonUserW("SandboxUser", password) 获取 primary token
  → CreateRestrictedToken(primary_token) 创建受限版本
  → CreateProcessAsUserW(restricted_token, "cmd.exe")
     ↑ 这里失败！需要 SeAssignPrimaryTokenPrivilege
  ```

**即使 broker 以管理员运行**，`SeAssignPrimaryTokenPrivilege` 默认不授予任何账户（除了 SYSTEM），需要在安全策略中显式添加。这意味着没有可靠的方式从外面启动另一个用户的受限进程。

两跳启动绕开这个问题：

  ```
broker.exe (真实用户)
  → CreateProcessWithLogonW("SandboxUser", password, "runner.exe")
     ↑ 成功！因为只传用户名密码，不传 Token

runner.exe (已在 SandboxUser 上下文中运行)
  → OpenProcessToken(GetCurrentProcess())  ← 拿的是 SandboxUser 的 token
  → CreateRestrictedToken(...)             ← 在自己的上下文中创建受限版本
  → CreateProcessAsUserW(restricted_token, "cmd.exe")
     ↑ 成功！因为 runner 本身就是 SandboxUser，
        在自己的进程上下文中操作自己的 token
  ```

**核心原理**：`CreateRestrictedToken` 和 `CreateProcessAsUserW` 放在**目标用户自己的进程上下文中**调用，不需要跨用户边界的特权。

### 2.6 WFP（Windows Filtering Platform）

**WFP 是 Windows Vista+ 的内核级网络流量过滤框架。** 它比 Windows Firewall API 更底层、更灵活。

#### 防火墙层级对比

  ```
应用层   ← netsh advfirewall / Windows Firewall API
  ↕
WFP 层   ← ★ srt 使用的层级（kernel-mode filtering engine）
  ↕
TCP/IP 栈
  ↕
网卡驱动
  ```

#### WFP Filter 的关键组件

  ```
WFP Filter
├── Layer           ← 在哪一层拦截（outbound transport / inbound / ALE connect）
├── Sublayer        ← 优先级（属于哪个 sublayer 决定了 filter 的执行顺序）
├── Conditions[]    ← 匹配条件
│   ├── FWPM_CONDITION_ALE_USER_ID  ← ★ 按用户 SID 匹配！
│   ├── FWPM_CONDITION_IP_REMOTE_ADDRESS
│   └── FWPM_CONDITION_IP_REMOTE_PORT
└── Action          ← Permit / Block / Callout
  ```

#### srt 的 WFP 配置

  ```
Filter 1: Block all outbound
  Condition: ALE_USER_ID == srt-sandbox SID
  Action: Block

Filter 2: Permit loopback proxy
  Condition: ALE_USER_ID == srt-sandbox SID
             AND IP_REMOTE_ADDRESS == 127.0.0.1
             AND IP_REMOTE_PORT in [60080..60089]
  Action: Permit
  ```

两个 filter 的组合实现了：沙箱进程只能连接 `127.0.0.1:60080-60089`，其余出站全部被 Block filter 拦截。

#### 为什么不用 Windows Firewall API

Codex 用了 Firewall API 来配置用户级出站规则。WFP 相对 Firewall API 的优势：

| 维度         | Windows Firewall              | WFP                                                   |
| ------------ | ----------------------------- | ----------------------------------------------------- |
| 粒度         | 进程路径 / 端口 / 用户整体    | IP + Port + UserSID + AppID 组合                      |
| 优先级控制   | 有限的 rule ordering          | 精确的 sublayer + filter weight                       |
| 旁路风险     | 程序可以添加自己的 Allow 规则 | WFP callout 在 kernel 执行，用户态无法添加同类 filter |
| 管理工具可见 | 可用 `wf.msc` 查看编辑        | 需要 WFP-specific 工具                                |
| 稳定性       | 可能被组策略覆盖              | 独立于防火墙配置，不被组策略影响                      |

> **一句话总结**：Firewall API 是说"请帮我拦截"，WFP 是说"我直接在内核拦截"。

### 2.7 Job Objects

**Job Object 是 Windows 中"进程组资源限制"的机制**，等价于 Linux cgroup。

一个 Job Object 可以包含多个进程，施加以下限制：

  ```
Job Object
├── 进程数上限 (ActiveProcessLimit)
├── 内存上限 (ProcessMemoryLimit / JobMemoryLimit)
├── CPU 速率限制 (CpuRate, 以 0.01% 为单位)
├── 如果 Job 关闭，所有成员进程被强制终止
├── 禁止成员进程创建新的桌面 (breakaway OK/not OK)
└── UI 限制 (禁止读取剪贴板、禁止访问全局 atoms 表等)
  ```

**关键行为**：

- 子进程**自动继承**父进程的 Job Object（除非指定 `CREATE_BREAKAWAY_FROM_JOB`）
- 一个进程只能属于一个 Job Object
- 如果 Job 设置了 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`，关闭 Job handle = 杀死所有成员进程

这天然适合沙箱场景：创建沙箱时创建 Job，把所有沙箱进程 assign 进去，沙箱销毁时关掉 Job → 所有子进程被内核强制清理，不留僵尸进程。

#### 与 cgroup 的对比

| Linux cgroup v2     | Windows Job Object                                        |
| ------------------- | --------------------------------------------------------- |
| `memory.max`        | `JOBOBJECT_EXTENDED_LIMIT_INFORMATION.ProcessMemoryLimit` |
| `cpu.max`           | `JOBOBJECT_CPU_RATE_CONTROL_INFORMATION.CpuRate`          |
| `pids.max`          | `JOBOBJECT_BASIC_LIMIT_INFORMATION.ActiveProcessLimit`    |
| `cgroup.kill`       | `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`                      |
| 层级 cgroup（父子） | 不支持层级 Job Objects                                    |

---

## 三、Codex Windows 沙箱分析

来源：[Building a safe, effective sandbox to enable Codex on Windows](https://openai.com/index/building-codex-windows-sandbox/)

### 3.1 设计目标

- Agent 需要操作用户**真实的文件、工具链、环境**，不能用 VM 隔离
- 默认允许 reads 几乎任何地方，但 **writes 限制在工作目录内**，**网络默认断开**
- 需要 OS 级强制（非 advisory），而不是仅靠环境变量或弹窗确认

### 3.2 否决的方案

| 方案                | 否决原因                                                     |
| ------------------- | ------------------------------------------------------------ |
| **AppContainer**    | 需要预声明所有能力；Codex 驱动开放式开发工作流，无法提前枚举 |
| **Windows Sandbox** | 全新 VM 桌面，无法操作用户真实工作目录；Windows Home 不提供  |
| **MIC 完整性标签**  | 把工作目录降级为 low integrity = 所有 low-integrity 进程都能写入，污染主机信任模型 |

### 3.3 第一代：unelevated 沙箱（免提权）

#### 文件写入控制——SID + Write-Restricted Token

这是核心创新。Write-Restricted Token 对每次写操作执行**双重 ACL 检查**：

  ```
传统检查：用户 ACL → 通过 ✓ → 可写
双重检查：用户 ACL → 通过 ✓ → 再看 Restricted SID 列表 → 至少一个 SID 匹配 → 才可写
  ```

具体步骤：

    1. 创建合成 SID（`sandbox-write`）——不关联真实用户，仅作权限标记
    2. 给这个 SID 授予**仅工作目录和 writable_roots** 的写/执行/删除权限
    3. 明确拒绝它对 `.git/`、`.codex/`、`.agents/` 等敏感子目录的写权限
    4. 启动命令时使用 Write-Restricted Token，受限 SID 列表 = `[Everyone, 当前登录会话SID, sandbox-write]`

**效果**：即使文件系统的 ACL 允许当前用户写，但 `sandbox-write` SID 没被授权的地方照样写不了。

#### 网络控制——环境变量污染（已废弃）

因为没管理员权限装不了防火墙，采用了"下毒"策略：

  ```
HTTPS_PROXY=http://127.0.0.1:9    # 死端口
ALL_PROXY=http://127.0.0.1:9
GIT_PROXY=http://127.0.0.1:9
GIT_SSH_COMMAND=cmd /c exit 1
  ```

在 PATH 前插入 `denybin/` 目录，放 SSH/SCP stub 抢占优先级。

**致命缺陷**：纯 advisory，任何程序可以忽略环境变量或直接开 socket。

### 3.4 第二代：elevated 沙箱（当前生产方案）

接受管理员提权，换取真正的网络隔离。

#### 核心架构：四层模型

  ```
┌─────────────────────────────────────────────┐
│  codex.exe                                  │  普通用户进程，不要求 admin
│  (用户交互 + Agent 逻辑 + Markdown 渲染)    │
├─────────────────────────────────────────────┤
│  codex-windows-sandbox-setup.exe            │  独立二进制，仅跨 UAC 时运行
│  • 创建合成 SID (sandbox-write)             │
│  • 创建两个本地用户：                        │
│    - CodexSandboxOffline (被防火墙拦截)      │
│    - CodexSandboxOnline  (不被拦截)          │
│  • 配置防火墙出站拦截规则                    │
│  • 异步安装常用目录读权限 ACL               │
│     (C:\Users\<user>\, C:\Windows\,          │
│      C:\Program Files\ 等)                   │
├─────────────────────────────────────────────┤
│  codex-command-runner.exe                   │  以沙箱用户身份运行
│  • 提取自身 token → GetTokenInformation     │
│  • CreateRestrictedToken 创建受限 token      │
│  • CreateProcessAsUserW 启动子进程           │
├─────────────────────────────────────────────┤
│  子进程 (git.exe / python.exe / cmd.exe)     │  Write-Restricted Token
│  + 防火墙规则拦截所有出站                    │
└─────────────────────────────────────────────┘
  ```

#### 为什么需要 codex-command-runner

Codex 最初想走简单路径：

  ```
codex.exe → LogonUserW(沙箱用户) → CreateRestrictedToken → CreateProcessAsUserW → 子进程
  ```

但 `CreateProcessAsUserW` 在这里有**权限墙**——从真实用户侧调用它来启动另一个用户的受限 token 进程是不允许的。

**解决方案（两跳启动）**：

    1. `codex.exe` 用 `CreateProcessWithLogonW` 启动 `codex-command-runner.exe`（以沙箱用户身份，token 未受限）
    2. `codex-command-runner.exe` 在沙箱用户上下文中，打开自己的 token → 提取登录 SID → 调用 `CreateRestrictedToken` → 用 `CreateProcessAsUserW` 启动真正要跑的子进程

所有限制逻辑在沙箱用户侧完成，规避了跨用户边界的 token 操作限制。

#### 独立 Setup 二进制设计理由

- `codex.exe` 保持非提权进程，仅 setup 时 UAC
- Windows 专用代码不污染跨平台主二进制
- 长时间 ACL 遍历不绑定主进程生命周期
- decoupled longer-running setup work from the lifetime of the main process

#### 读权限问题

进程以 `CodexSandboxOffline` 身份运行，默认读不了真实用户的 profile 目录（Windows 禁止跨用户读取）。解决：设置阶段对常用目录安装读 ACL。因为 ACL 深层遍历开销大，**异步执行**不阻塞用户。

---

## 四、Claude Code sandbox-runtime (srt) Windows 沙箱分析

来源：[anthropic-experimental/sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime)

### 4.1 总体定位

**srt** 是一个跨平台进程沙箱工具（TypeScript 实现，npm 包），为 Claude Code 提供 OS 级文件和网络隔离：

| 平台        | 隔离原语                          | 网络隔离                                     |
| ----------- | --------------------------------- | -------------------------------------------- |
| macOS       | `sandbox-exec` + Seatbelt profile | proxy-based (loopback only)                  |
| Linux       | `bubblewrap` + network namespace  | Unix socket proxy in host netns              |
| **Windows** | **srt-win.exe (Rust)** + 独立用户 | **WFP (Windows Filtering Platform)** + proxy |

### 4.2 Windows 实现：`srt-win.exe`

**Windows 后端模块**：`src/sandbox/windows-sandbox-utils.ts`

#### 架构

  ```
┌──────────────────────────────────────────┐
│  srt CLI (TypeScript / Node.js)          │  用户进程
├──────────────────────────────────────────┤
│  srt-win.exe (Rust helper binary)        │  需管理员权限安装
│  • install: 创建 srt-sandbox 本地用户    │
│  • 安装 machine-wide WFP filter set      │
│  • exec: 两跳启动子进程                  │
│     broker → CreateProcessWithLogonW     │
│           → srt-win runner (sandbox用户) │
│           → CreateRestrictedToken        │
│           → 子进程                       │
├──────────────────────────────────────────┤
│  JS 层: HTTP/SOCKS proxy multiplex       │  运行在 host 上
│  监听 60080-60089 范围                   │
│  • HTTP proxy (HTTP/HTTPS 转发)          │
│  • SOCKS5 proxy (其他 TCP 协议)          │
├──────────────────────────────────────────┤
│  子进程 (被 WFP 过滤 + Restricted Token) │
│  仅允许 loopback 连接到 proxy 端口范围    │
└──────────────────────────────────────────┘
  ```

#### 两跳启动与 Codex 的高度相似性

两者命中相同的 Windows 限制，采用相同的解法：

  ```
Codex:       cmd-runner.exe → CreateProcessWithLogonW → CreateRestrictedToken → CreateProcessAsUserW
Claude srt:  srt-win exec   → CreateProcessWithLogonW → CreateRestrictedToken → CreateProcessAsUserW
  ```

这个架构在两个项目中**基本一致**。

#### WFP (Windows Filtering Platform) vs Windows Firewall

srt 使用 **WFP**（更底层的 API）而非 Windows Firewall API，这是与 Codex 的主要技术差异：

- Codex：通过 `netsh advfirewall` 或其他 Firewall API 创建用户级出站规则
- srt：直接在 WFP 层安装 filter set，keyed on `srt-sandbox` 用户的 SID

WFP 的优势：

- 更细粒度的控制（kernel-mode filtering engine）
- 可以写"允许 loopback 到 60080-60089，其余全部拦截"
- 不受防火墙 UI 或组策略影响

**WFP filter 逻辑**：

    1. `install` 阶段安装 machine-wide filter set
    2. 对所有出站连接，检查进程运行用户的 SID
    3. 如果是 `srt-sandbox` 用户 → 仅允许目的地为 `127.0.0.1:60080-60089` 的流量
    4. 其余所有 `srt-sandbox` 的出站流量全部丢弃

**绕过保护**：独立的沙箱用户 SID 保证了**即使子进程通过 surrogate spawn（schtasks、BITS job、COM RunAs 等）产生新的子进程，WFP filter 依然生效**——因为新进程的 token 仍携带 `srt-sandbox` 的 principal SID。

#### 文件系统隔离——ACE 白名单

  ```
denyRead  → 添加显式 DENY ACE（对 sandbox SID）
denyWrite → 添加显式 DENY ACE
allowRead → 添加显式 ALLOW ACE
allowWrite → 添加显式 ALLOW ACE
  ```

通过 `grantWindowsAcl` / `stampWindowsAcl` 函数对指定路径动态修改 ACL。

规则语义与 Linux/macOS 对齐：

- **Read**: deny-then-allow（默认全部可读，先 deny 再 allow 覆盖）
- **Write**: allow-only（默认全部不可写，只有 allow 列出的路径可写）

#### 防止沙箱逃逸的设计

srt 文档明确指出独立用户账户**结构性关闭了以下逃逸路径**：

- `schtasks` 注册任务——注册到 `srt-sandbox` 身份下，无法以真实用户运行
- `PROC_THREAD_ATTRIBUTE_PARENT_PROCESS`——无法跨用户边界
- BITS jobs——under `srt-sandbox`
- COM `RunAs="Interactive User"`——另一个用户 SID，无法到达真实用户的桌面会话

#### JS Proxy 复用层

srt 的关键创新是 **JS 层 HTTP/SOCKS proxy** 复用：

- TypeScript 实现，轻量级的 HTTP 和 SOCKS5 代理
- WFP filter 允许 sandbox 进程连接到 127.0.0.1:60080-60089
- 所有出网流量必须经过这个代理
- 代理在应用层执行 domain allowlist/denylist 过滤
- 一套代理服务多个沙箱进程

### 4.3 srt 架构总结

| 组件                       | 语言             | 职责                              |
| -------------------------- | ---------------- | --------------------------------- |
| `srt` CLI                  | TypeScript (npm) | 入口，配置解析，platform dispatch |
| `sandbox-manager.ts`       | TypeScript       | 初始化 proxy、调用平台 utils      |
| `windows-sandbox-utils.ts` | TypeScript       | 调用 srt-win.exe，ACL 设置        |
| `srt-win.exe`              | Rust             | 用户创建、WFP filter、进程启动    |
| `http-proxy.ts`            | TypeScript       | HTTP/HTTPS 代理 + domain 过滤     |
| `socks-proxy.ts`           | TypeScript       | SOCKS5 代理 + domain 过滤         |

---

## 五、Codex vs srt 对比总结

| 维度           | Codex                                      | Claude srt                                |
| -------------- | ------------------------------------------ | ----------------------------------------- |
| **主语言**     | Rust (codex.exe) + setup.exe + runner.exe  | TypeScript (wrapper) + Rust (srt-win.exe) |
| **沙箱用户**   | CodexSandboxOffline / CodexSandboxOnline   | srt-sandbox                               |
| **文件隔离**   | Write-Restricted Token + 合成 SID ACL      | 显式 DENY/ALLOW ACE + 独立用户身份        |
| **网络隔离**   | Windows Firewall 用户级出站规则            | **WFP** kernel-mode filter set (更强)     |
| **网络代理**   | 未公开细节                                 | JS 层 HTTP/SOCKS proxy (60080-60089)      |
| **运行模式**   | Elevated（默认）/ Unelevated（降级）       | Elevated（需要 install 步骤）             |
| **两跳启动**   | ✅ CreateProcessWithLogonW → runner → child | ✅ 同架构                                  |
| **setup 解耦** | 独立 setup.exe 二进制                      | srt-win install 子命令                    |
| **私有桌面**   | ✅ Private Desktop for UI isolation         | 未实现                                    |

---

## 六、JiuwenBox Windows 沙箱移植方案

### 6.0 架构总览

#### 核心策略：扩展现有架构，不替代

JiuwenBox 的 Linux 实现通过 `server/runtime/process.py`（`ProcessRuntime`）统一调度 `supervisor/` 下的 bwrap/landlock/seccomp/network/cgroup 模块完成沙箱创建。Windows 移植**继承同一套管理面 API**（FastAPI → SandboxManager → Policy Engine），在运行时层新增平台分支。

核心思路：**保留上层不变，Linux 路径不改一行代码，仅在需要处加 `if sys.platform == "win32"` 分支。**

#### 模块调用关系

> **说明**：实线箭头 = 运行时调用 · 虚线箭头 = 初始化/部署阶段

  ```mermaid
graph TB
    subgraph api["管理面 (不改动)"]
        A1["FastAPI ServerREST API · 生命周期 · 审计"]
        A2["SandboxManager创建/销毁 · 空闲回收"]
        A3["Policy EngineYAML解析 · 策略校验"]
    end

    subgraph setup["一次性 Setup (管理员提权)"]
        B1["用户 & 组创建jbx-sandbox 本地账户"]
        B2["WFP 过滤规则fwpuclnt.dll · ctypes"]
        B3["读 ACL 预装SystemRoot/UserProfile"]
    end

    subgraph runtime["沙箱运行时 (每次exec触发)"]
        C1["文件 ACL 施加win_acl.py · DENY/ALLOW"]
        C2["两跳进程启动Broker→Logon→Runner→RestrictedToken"]
        C3["Job Object 限制CPU/内存/进程数"]
        C4["出站代理HTTP CONNECT · SOCKS5"]
    end

    subgraph kernel["Windows 内核安全边界"]
        D1["文件系统隔离NTFS DACL · 合成SID"]
        D2["进程隔离独立用户 · Restricted SIDs"]
        D3["网络隔离(WFP)按SID过滤 · 仅放行loopback"]
    end

    subgraph linux["Linux 路径 (完全不变)"]
        E1["bwrap · landlock · seccompnetwork · cgroup · daemon_ipc"]
    end

    A1 --> A2
    A3 --> A2
    A2 -->|"Linux 路径"| E1
    A2 -->|"Windows 路径"| C1
    A1 -.->|"setup阶段"| B1
    B1 --> B2 --> B3
    C1 --> C2 --> C3
    C1 --> D1
    C2 --> D2
    C3 --> D2
    C2 --> D3
    D3 -->|"仅放行loopback"| C4
    C4 -->|"代理出站"| Internet((Internet))

    style api fill:#dbeafe,stroke:#2196f3
    style setup fill:#fef3c7,stroke:#f59e0b
    style runtime fill:#ede9fe,stroke:#8b5cf6
    style kernel fill:#fce7f3,stroke:#ec4899
    style linux fill:#dcfce7,stroke:#22c55e
  ```

> **说明**：实线 = 运行时调用 · 虚线 = 初始化/部署阶段 · 蓝=管理面 黄=Setup 紫=运行时 粉=内核 绿=Linux

#### Windows 沙箱安全功能

| 安全维度         | 实现机制                                                     |
| ---------------- | ------------------------------------------------------------ |
| **文件系统隔离** | 合成 SID + NTFS DACL（DENY/ALLOW ACE），Write-Restricted Token 双重 ACL 检查。默认全部不可写，仅白名单路径可写。 |
| **进程隔离**     | 独立本地用户 `jbx-sandbox` + Restricted Token（剥离管理员组和特权）+ 两跳启动模型（CreateProcessWithLogon → RestrictedToken → CreateProcessAsUser）。沙箱进程无法跨用户边界操作。 |
| **网络隔离**     | WFP（Windows Filtering Platform）内核级过滤，按沙箱用户 SID Block 所有出站流量。不依赖应用层代理，即使子进程直接开 socket 也被拦截。 |
| **资源限制**     | Job Object：内存上限（ProcessMemoryLimit）、CPU 速率（CpuRate）、进程数上限（ActiveProcessLimit）。Job 关闭时内核强制终止所有成员进程（KILL_ON_CLOSE）。 |

#### 需要改动的文件清单

| 文件                        | 改动类型 | 说明                                                         |
| --------------------------- | -------- | ------------------------------------------------------------ |
| `server/runtime/process.py` | **修改** | `start_sandbox()` / `exec_in_sandbox()` / `stop_sandbox()` 各加 `if sys.platform == "win32"` 分支 |
| `server/app.py`             | **修改** | lifespan startup 中加 Windows 检查（检查安装标记）           |
| `supervisor/win_setup.py`   | **新增** | 一次性环境准备（用户创建、WFP filter 安装、读 ACL 预装）     |
| `supervisor/win_exec.py`    | **新增** | 两跳启动沙箱子进程                                           |
| `supervisor/win_acl.py`     | **新增** | 文件系统 ACL 的施加与撤销                                    |
| `supervisor/win_proxy.py`   | **新增** | HTTP + SOCKS5 代理，域名/IP 白名单/黑名单过滤                |
| `supervisor/win_job.py`     | **新增** | Job Object 资源限制（CPU/内存/进程数）                       |
| `models/policy.py`          | **不改** | 现有 YAML policy schema 完全兼容，语义映射在运行时完成       |
| `server/sandbox_manager.py` | **不改** | 沙箱生命周期管理逻辑平台无关                                 |
| `server/policy_engine.py`   | **不改** | Policy 解析和验证逻辑平台无关                                |
| `server/audit_logger.py`    | **不改** | 审计日志逻辑平台无关                                         |

**不改的文件**（Linux 路径完全不受影响）：
`supervisor/bwrap.py`、`supervisor/landlock.py`、`supervisor/seccomp.py`、`supervisor/network.py`、`supervisor/cgroup.py`、`supervisor/daemon_ipc.py`、`supervisor/sandbox_daemon.py`、`supervisor/landlock_launcher.py`

### 6.1 移植策略

与 Linux 版本一致，采用**需要特权的一次性 setup** + **非特权运行时**的模式（Linux 需要 root/CAP_SYS_ADMIN 启动 bubblewrap，Windows 需要管理员权限完成一次性环境准备）。不做免提权的降级方案，因为那会牺牲真正的网络隔离能力。

全 Python 实现，不引入编译语言依赖。Windows API 调用通过 `ctypes`（`advapi32.dll`、`kernel32.dll`、`fwpuclnt.dll`）和 `pywin32`（`win32security`、`win32net`、`win32process`）完成。

### 6.2 整体启动流程

  ```
用户（或 agent-server）启动 jiuwenbox-server
    │
    ├─ lifespan startup 阶段：
    │    ├─ 如果不存在 → 通过 UAC (runas verb) 执行自己的 setup 子进程
    │    │    ├─ win_setup.py: 创建 jbx-sandbox 用户
    │    │    ├─ win_setup.py: 安装 WFP block/permit filter set
    │    │    └─ win_setup.py: 异步预装常用目录读 ACL（后台线程）
    │    │
    │    │
    │    └─ 启动 SandboxManager（idle reaper 等）
    │
    └─ 每次创建沙箱 (POST /api/v1/sandboxes)：
         ├─ SandboxManager.create_sandbox()
         ├─ Policy Engine 解析 policy.yaml → 生成 effective policy
         └─ ProcessRuntime.start_sandbox():
              if sys.platform == "win32":
              ├─ win_acl.py: 对 workspace 施加读写 ACL（合成 SID）
              ├─ win_exec.py: 两跳启动 → Restricted Token 子进程
              ├─ win_job.py: 可选 Job Object 资源限制
              └─ 返回 SandboxRef {id, phase="ready", pid, runtime="process"}
              else:
              └─ （现有 bwrap + landlock + seccomp + network + cgroup 路径，完全不变）
  ```

### 6.3 模块划分

移植在 `supervisor/` 下新增六个 Python 模块，与 `bwrap.py`、`landlock.py`、`seccomp.py` 等现有模块**并列共存**：

| 模块                      | 职责                                                     | 关键依赖                     | 提权需求   |
| ------------------------- | -------------------------------------------------------- | ---------------------------- | ---------- |
| `supervisor/win_setup.py` | 一次性环境准备（用户创建、WFP filter 安装、读 ACL 预装） | `ctypes` (WFP) + `pywin32`   | **管理员** |
| `supervisor/win_exec.py`  | 两跳启动沙箱子进程                                       | `ctypes` (advapi32/kernel32) | 无需       |
| `supervisor/win_acl.py`   | 文件系统 ACL 的施加与撤销（读写控制）                    | `pywin32` (`win32security`)  | 无需       |
| `supervisor/win_job.py`   | Job Object 资源限制（CPU/内存/进程数）                   | `ctypes` (kernel32)          | 无需       |

**Linux 模块完全不动**：`bwrap.py`、`landlock.py`、`landlock_launcher.py`、`seccomp.py`、`network.py`、`cgroup.py`、`daemon_ipc.py`、`sandbox_daemon.py` 保持原样。

### 6.4 win_setup.py —— 一次性环境准备

需要管理员权限执行，通过 `subprocess` 以 UAC `runas` verb 提权。**幂等**——重复执行无副作用，通过注册表标记判断是否已完成。

#### 6.4.1 创建专用本地用户

通过 `pywin32` 的 `win32net.NetUserAdd` 和 `win32net.NetLocalGroupAddMembers` 创建 `jbx-sandbox` 用户和 `jbx-sandbox-users` 组。设置随机密码，标记为 `UF_PASSWD_CANT_CHANGE | UF_DONT_EXPIRE_PASSWD`，并从登录界面隐藏。通过 `LookupAccountName` 获取该用户的 SID，后续所有安全操作以此 SID 为锚点。

#### 6.4.2 安装 WFP Filter Set

这是整个移植中技术难度最高的部分。WFP 的 user-mode API 位于 `fwpuclnt.dll`，通过 `ctypes` 加载调用。核心调用链：

  ```
FwpmEngineOpen(0, RPC_C_AUTHN_WINNT, ...)       → 打开 WFP 引擎会话
FwpmSubLayerAdd(engine, &sublayer, ...)          → 创建专用 sublayer
FwpmFilterAdd(engine, &block_filter, ...)        → 安装 Block filter
FwpmFilterAdd(engine, &permit_filter, ...)       → 安装 Permit filter
  ```

每个 API 都需要用 `ctypes.Structure` 精确定义对应的 C 结构体：`FWPM_SESSION`、`FWPM_SUBLAYER`、`FWPM_FILTER`、`FWPM_FILTER_CONDITION`、`FWP_BYTE_BLOB` 等。

两个 filter 的安装参数：

  ```
# Block filter（优先级较低）
Layer:     FWPM_LAYER_ALE_AUTH_CONNECT_V4 + V6
Sublayer:  JiuwenBox_Block (weight=100)
Conditions:
  - FWPM_CONDITION_ALE_USER_ID == jbx-sandbox SID
Action:    FWP_ACTION_BLOCK
Weight:    LOW

# Permit filter（优先级较高，覆盖 Block）
Layer:     FWPM_LAYER_ALE_AUTH_CONNECT_V4 + V6
Sublayer:  JiuwenBox_Permit (weight=200)
Conditions:
  - FWPM_CONDITION_ALE_USER_ID == jbx-sandbox SID
  - FWPM_CONDITION_IP_REMOTE_ADDRESS == 127.0.0.1
Action:    FWP_ACTION_PERMIT
Weight:    MEDIUM-HIGH (高于 Block)
  ```

**WFP ctypes 封装的降级方案**：如果 WFP 的 `ctypes` 封装在开发中遇到难以逾越的问题，可以降级为 `subprocess` 调用 PowerShell `New-NetFirewallRule -LocalUser jbx-sandbox`——功能等价，牺牲的是 WFP 在内核态的精细优先级控制和绕过保护。

#### 6.4.3 读权限预装

因为 `jbx-sandbox` 是独立用户，默认无法读取真实用户 profile。需要对 `%USERPROFILE%`、`%SystemRoot%`、`Program Files`、`ProgramData` 等常用目录递归施加 Allow Read ACE。通过 `win32security.GetNamedSecurityInfo` / `SetNamedSecurityInfo` 完成，**在后台线程中异步执行**不阻塞 install 返回。预装进度写入注册表标记，支持断点续传。

### 6.5 win_exec.py —— 两跳启动

不需要管理员权限。通过 `ctypes` 调用 `advapi32.dll` 和 `kernel32.dll` 中的 Windows 进程创建 API。

与 Linux 版本保持同一 Runnable 接口（`start_sandbox()` → 返回 pid），内部实现完全不同：

**第一跳（Server 进程，broker 侧）：**

    1. 调用 `CreateProcessWithLogonW`，以 `jbx-sandbox` 用户身份启动一个新的 Python 子进程（执行自身 `win_exec.py` 的 runner 入口函数）

**第二跳（Runner 进程，已在 jbx-sandbox 上下文中）：**

    1. `OpenProcessToken(GetCurrentProcess())` 打开自己的 token
    2. `GetTokenInformation(hToken, TokenGroups)` 提取登录会话 SID
    3. `CreateWellKnownSid(WinWorldSid)` 获取 Everyone SID
    4. `AllocateAndInitializeSid(...)` 创建合成 SID（JHXSandboxWrite）
    5. `CreateRestrictedToken` 创建 Write-Restricted Token，受限 SID 列表 = `[Everyone, LogonSession, JHXSandboxWrite]`，Flags = `DISABLE_MAX_PRIVILEGE | WRITE_RESTRICTED | SANDBOX_INERT`
    6. `CreateProcessAsUserW` 以受限 token 启动目标命令
    7. 写入 sandbox_id + workspace 到 stdout pipe → Server 进程读取

两步之间用 stdin/stdout pipe 传递 exit code。两跳的必要性在于 `CreateProcessAsUserW` 的 `SeAssignPrimaryTokenPrivilege` 权限墙——将 Restricted Token 的创建放在 runner 自己的上下文中完成，绕过了跨用户边界的权限限制。

### 6.6 win_proxy.py —— 出站流量代理

纯 Python 标准库实现，依赖 syncio。作为 asyncio task 运行在 server 进程内，在 127.0.0.1 的固定端口范围（60080-60089）同时监听：

- **HTTP CONNECT 隧道**：用于 HTTPS 流量（透传 TLS，不做中间人）
- **SOCKS5**：用于非 HTTP 协议（如 Git SSH、数据库连接等）

代理过滤逻辑：解析目标域名/IP → 先检查 deny 列表（命中则拒绝）→ 再检查 allow 列表（非空且未命中则拒绝）→ 放行建立隧道。与 Linux 版本的 supervisor/network.py 对应的 egress/ingress 规则语义完全对齐。

**启动时机**：在 pp.py 的 lifespan startup 中作为 asyncio task 初始化一次，所有沙箱共享一个 proxy 实例。

**沙箱内环境变量**：子进程启动时自动设置 HTTP_PROXY、HTTPS_PROXY、ALL_PROXY 指向 proxy 端口。即使子进程不遵守环境变量（直接创建 socket），WFP filter 也会在 kernel 层拦截，确保唯一出口是 proxy。

> **为什么需要代理层**：WFP 只能做 IP:Port 级的二元过滤（放行/阻断），无法解析域名。在 CDN 时代一个 IP 背后有数百个域名，需要代理层在 HTTP CONNECT / SOCKS5 握手阶段提取目标域名，实现细粒度的域名白名单/黑名单控制。

### 6.7 win_acl.py —— 文件系统权限控制

通过 `pywin32` 的 `win32security` 模块操作 DACL。与 Linux Landlock 语义对齐：

**读控制：deny-then-allow 模式。** 默认全部可读（依赖 install 阶段的预装读 ACL）。deny 列表施加 Deny Read ACE；allow 列表施加 Allow Read ACE 覆盖 deny。

**写控制：allow-only 模式。** 默认全部不可写（独立用户 + Write-Restricted Token）。allow 列表对指定路径施加 Allow Write + Execute + Delete ACE（keyed on 合成 SID）；deny 列表在 allow 覆盖的范围内做精细化封锁（如 `.git/`、`.env`、`.agents/` 等）。

  ```python
# win_acl.py 核心函数
def apply_sandbox_acl(workspace: str, allow_write: list[str], deny_write: list[str]):
    sid = get_synthetic_sid()  # 获取合成 JHXSandboxWrite SID
    for path in allow_write:
        grant_ace(path, sid, ["Write", "Execute", "Delete"], recursive=True)
    for path in deny_write:
        grant_ace(path, sid, ["Write"], mode="DENY", recursive=True)

def revoke_sandbox_acl(workspace: str):
    remove_aces(workspace, get_synthetic_sid())
  ```

SandboxManager 在 `create_sandbox()` 时调用 apply，在 `delete_sandbox()` 时调用 revoke。

### 6.8 win_job.py —— 资源限制

将 Linux `supervisor/cgroup.py` 的能力映射到 Windows Job Objects：

- 内存上限 → `JobObjectExtendedLimitInformation.ProcessMemoryLimit`
- CPU 速率 → `JobObjectCpuRateControlInformation.CpuRate`
- 进程数上限 → `JobObjectBasicLimitInformation.ActiveProcessLimit`
- 全部清理 → `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`

子进程通过 SUSPEND → `AssignProcessToJobObject` → Resume 自动继承 Job Object。关闭 Job handle（沙箱销毁时）= 内核强制终止所有成员进程。

### 6.9 Server 运行时适配

#### ProcessRuntime 改动（process.py）

  ```python
class ProcessRuntime(RuntimeAdapter):
    async def start_sandbox(self, sandbox_id, policy, ...):
        if sys.platform == "win32":
            return await self._start_sandbox_windows(sandbox_id, policy, ...)
        else:
            # 现有 Linux 逻辑，一行不动
            return await self._start_sandbox_linux(sandbox_id, policy, ...)

    async def _start_sandbox_windows(self, sandbox_id, policy, ...):
        # 1. 确保 setup 完成
        await ensure_windows_setup()
        # 2. 施加文件 ACL
        apply_sandbox_acl(workspace, policy.allow_write, policy.deny_write)
        # 3. 两跳启动
        runner_pid, runner_pid = await win_exec_two_hop(sandbox_id, policy)
        # 4. Job Object（可选）
        if policy.cgroup:
            job_handle = create_job(policy.cgroup.memory_max, ...)
            assign_to_job(job_handle, runner_process_handle)
        return SandboxRef(id=sandbox_id, phase="ready", pid=runner_pid, ...)
  ```

#### app.py 的 lifespan 改动

  ```python
# lifespan startup 中：
if sys.platform == "win32":
    ensure_windows_install()
    proxy_task = asyncio.create_task(serve_windows_proxy())
else:
    # 现有的 Linux 初始化逻辑
    enable_child_subreaper()
    # ...
  ```

**沙箱销毁时**：

  ```python
# sandbox_manager.delete_sandbox() 中：
if sys.platform == "win32":
    revoke_sandbox_acl(workspace)
    kill_runner_process(runner_pid)  # → 关闭 Job Object → 所有子进程被内核强制终止
else:
    # bwrap teardown via daemon_ipc.shutdown()
  ```

### 6.10 与现有 Policy 模型的兼容

现有 YAML policy schema 需要小幅调整：将原有 Linux 专用字段收敛到一级字段 `bubblewrap` 下（不影响现有 Linux 解析逻辑），新增 `windows` 一级字段存放 Windows 专用配置。

#### 调整前（现有 schema）

  ```yaml
# 所有 Linux 隔离字段平铺在顶层
filesystem_policy:
  read_only: [...]
  read_write: [...]
  bind_mounts: [...]
namespace:
  user: true
  pid: true
  network: true
landlock:
  compatibility: v4
network:
  mode: isolated
cgroup:
  memory_max: 512M
  cpu_max: 1
  ```

#### 调整后

  ```yaml
# bubblewrap: 原有 Linux 字段（无感知迁移，兼容旧版）
bubblewrap:
  filesystem_policy:
    read_only: [...]
    read_write: [...]
    bind_mounts: [...]
  namespace:
    user: true
    pid: true
    network: true
  landlock:
    compatibility: v4
  network:
    mode: isolated
  cgroup:
    memory_max: 512M
    cpu_max: 1

# windows: Windows 专用配置（新增）
windows:
  proxy:
    port_range_start: 60080
    port_range_end: 60089
  filesystem:
    read_acl_preinstall:         # 一次性预装读 ACL
      - "%USERPROFILE%"
      - "%SystemRoot%"
      - "%ProgramFiles%"
    allow_write:                 # 沙箱可写路径
      - "{{ workspace }}"
    deny_write:                  # 精细化封锁
      - "{{ workspace }}/.git"
      - "{{ workspace }}/.env"
  network:
    mode: wfp_loopback_proxy      # WFP 仅放行 loopback → 代理
  resource:
    memory_max: 512M
    cpu_rate: 50                 # 百分比
    max_processes: 32
  ```

> **兼容性**：Policy Engine 在解析时先检查顶层 `bubblewrap` 字段是否存在；若不存在则兼容旧版扁平 schema（视为 `bubblewrap` 内容）。`windows` 字段仅在 `sys.platform == "win32"` 时读取。

#### Policy 字段语义映射

| Policy 字段                                | Linux 实现                   | Windows 映射                                       |
| ------------------------------------------ | ---------------------------- | -------------------------------------------------- |
| `bubblewrap.filesystem_policy.read_only`   | bind mount ro                | Allow Read ACE（预装阶段已处理）                   |
| `bubblewrap.filesystem_policy.read_write`  | bind mount rw                | Allow Write ACE for 合成 SID                       |
| `bubblewrap.filesystem_policy.bind_mounts` | bwrap --bind                 | 不适用 — Windows 路径直接可达                      |
| `bubblewrap.namespace.*`                   | User/PID/IPC/Cgroup/UTS ns   | 忽略（独立用户 = user ns 等价；Job = cgroup 等价） |
| `bubblewrap.landlock.*`                    | Landlock ABI                 | 忽略（ACL 替代）                                   |
| `bubblewrap.network.*`                     | network namespace + iptables | 忽略（WFP 替代）                                   |
| `bubblewrap.cgroup.*`                      | cgroup v2                    | 忽略（Job Object 替代）                            |
| `windows.filesystem.allow_write`           | —                            | Allow Write ACE for 合成 SID                       |
| `windows.filesystem.deny_write`            | —                            | Deny Write ACE for 合成 SID                        |
| `windows.filesystem.read_acl_preinstall`   | —                            | 一次性预装 Allow Read ACE 的路径列表               |
| `windows.network.mode`                     | —                            | `wfp_block_all` ╱ 未来可扩展                       |
| `windows.resource.memory_max`              | —                            | Job Object ProcessMemoryLimit                      |
| `windows.resource.cpu_rate`                | —                            | Job Object CpuRate（百分比）                       |
| `windows.resource.max_processes`           | —                            | Job Object ActiveProcessLimit                      |
| `timeout.idle_timeout`                     | idle reaper                  | 完全复用（平台无关）                               |
| `environment`                              | env vars 注入                | 完全复用（平台无关）                               |

### 6.11 实施顺序

分两阶段推进：第一阶段交付完整的 Windows 进程沙箱（文件隔离 + 进程隔离），第二阶段补全网络隔离与域名级过滤。

#### 第一阶段：Windows 进程沙箱（核心闭环）

| 步骤  | 内容                                                         | 依赖                       | 复杂度 |
| ----- | ------------------------------------------------------------ | -------------------------- | ------ |
| **1** | `win_acl.py` — 文件 ACL 管理                                 | `pywin32`                  | ⭐⭐     |
| **2** | `win_exec.py` — 两跳启动（先跑通进程隔离）                   | ctypes (kernel32/advapi32) | ⭐⭐⭐    |
| **3** | `process.py` — 平台分支 + 端到端流程（create → exec → delete） | 步骤1-2                    | ⭐⭐     |
| **4** | `win_setup.py` — 用户创建                                    | `pywin32`                  | ⭐⭐     |
| **5** | `win_job.py` — Job Object 资源限制                           | ctypes (kernel32)          | ⭐⭐     |
| **6** | `process.py` — 集成 Job Object                               | 步骤1-5                    | ⭐⭐     |
| **7** | 集成测试 — 验证文件隔离、进程隔离、进程逃逸                  | 步骤1-6                    | ⭐⭐⭐    |

> 第一阶段产出：沙箱进程文件隔离 + 进程隔离 + 资源限制。此阶段**不涉及网络隔离**，沙箱进程可直接访问外网。

#### 第二阶段：网络隔离（WFP + 出站代理）

| 步骤   | 内容                                                         | 依赖                  | 复杂度 |
| ------ | ------------------------------------------------------------ | --------------------- | ------ |
| **8**  | `win_setup.py` — WFP filter 安装（Block 所有外网直连 + Permit loopback） | ctypes (fwpuclnt.dll) | ⭐⭐⭐⭐   |
| **9**  | `win_proxy.py` — HTTP + SOCKS5 asyncio 代理                  | asyncio               | ⭐⭐⭐    |
| **10** | WFP Permit filter 对接代理端口（放开 loopback:60080-60089）  | 步骤8、9              | ⭐⭐     |
| **11** | app.py — 集成 proxy 生命周期                                 | 步骤9、10             | ⭐⭐     |
| **12** | 集成测试 — 验证 WFP 网络拦截、域名/IP 白名单/黑名单过滤      | 步骤8-11              | ⭐⭐⭐    |

> 第二阶段产出：WFP 拦截所有外网直连 + 代理层域名/IP 过滤。第二阶段部署前沙箱可直连外网；第二阶段部署后可精确控制沙箱能访问哪些外部资源。

---

#### 七、测试用例

### 7.1 Windows 测试用例

目前主要的测试用例是jiuwenbox/tests/integration/test_server_api_default.py，里面是对restful各个接口的端到端测试。测试时需要通过docker启动真实的Jiuwenbox服务，测试用例会连接该服务进行测试（见7.2）。

建议新增windows版本的接口测试用例：jiuwenbox/tests/integration/test_server_api_windows.py，对windows版本的沙箱进行测试。

### 7.2 Linux回归测试

引入 Windows 沙箱分支后，必须确保**不改动原有的 Linux 沙箱逻辑**。每次修改 `process.py` 或 `supervisor/` 下的平台分支代码后，执行以下回归测试：

#### 7.2.1 测试步骤

  ```bash
# 1. 获取 JiuwenBox 源码

git clone https://gitcode.com/openJiuwen/jiuwenswarm.git

cd jiuwenswarm/jiuwenbox

# 2. 构建并启动 Docker 容器（Linux 沙箱环境）

sudo ./scripts/build_docker.sh

sudo ./scripts/run_docker.sh

# 3. 运行集成测试

pytest tests/integration/test_server_api_default.py
  ```

#### 7.2.2 测试覆盖

`test_server_api_default.py` 验证 Linux 沙箱的核心能力不受影响：

- **沙箱创建/销毁** — `POST /api/v1/sandboxes` · `DELETE /api/v1/sandboxes/{id}`

- **命令执行** — `POST /api/v1/sandboxes/{id}/exec`

- **文件系统隔离** — bubblewrap bind mount + Landlock 生效

- **网络隔离** — network namespace + iptables 生效

- **资源限制** — cgroup v2 memory/cpu 限制生效

- **生命周期** — idle timeout 自动回收

