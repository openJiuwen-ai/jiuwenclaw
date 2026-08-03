# Commit 检视报告：82001d09 fix:全链路ok

## 一、概述

- Commit：`82001d099b645122d3eb89f652e102bd707eb4df`
- 日期：2026-08-01（作者 lby）
- 说明：Windows 沙箱适配链路终点（收尾）commit，约 276 增 109 删，3 文件。
- 定位：全链路打通的最后修复，此前约 20 个 commit 的收口。本次主要解决三类"链路最后一公里"问题：
  1. **exec 输出读取死锁**（`npx playwright install` 等大输出 child 把 64KB pipe 写满后 runner 仍在 wait 不读 → 互锁死等到 timeout 强杀，且强杀后孙进程持 pipe 写端不 EOF 把 runner accept 循环卡死，后续 exec 全 timeout 409）。
  2. **profile 环境变量补全**（第二跳 child env 来自 header 不含 `USERPROFILE/LOCALAPPDATA/APPDATA/TEMP`，Playwright 仅靠 `os.homedir()` fallback 才装对路径）。
  3. **WFP loopback 全端口放行**（pptx-craft 的 render server 用 `getPort()` 随机选端口，原 60080-60089 逐端口 Permit 无法覆盖 → `ERR_NETWORK_ACCESS_DENIED`）。
- 同时新增 runner 本地落盘日志（`C:\Users\jbx-sandbox\jiuwenbox-logs\<sandbox_id>\runner.log`），解决 control_port 回传链断/卡死时无可观测日志的问题。

## 二、变更范围

| 文件 | +/- | 说明 |
| --- | --- | --- |
| `jiuwenbox/src/jiuwenbox/supervisor/win_exec.py` | +302/-~60 | 重写 exec 子进程 stdout 读取逻辑（wait+drain 并行替代串行 wait-then-read，根治 pipe 写满死锁）；新增 runner 本地落盘日志（`_init_local_log`/`_local_log`/`get_sandbox_profile_dir`）；补全 child profile 环境变量（USERPROFILE/LOCALAPPDATA/APPDATA/TEMP 指向 jbx-sandbox profile）；新增 exec 心跳与启动/结束日志。 |
| `jiuwenbox/src/jiuwenbox/supervisor/win_wfp.py` | +~17/-~38 | Permit filter 从"逐端口（user+loopback+port）"改为"user+loopback 全端口放行"，解决 render server 随机端口被 Block；卸载侧兼容删旧 per-port 残留。 |
| `jiuwenbox/src/jiuwenbox/server/runtime/process.py` | +~21/-~7 | 第一跳 runner env 注入 TEMP/TMP 指向 jbx-sandbox profile 下每沙箱隔离子目录，补全 USERPROFILE/LOCALAPPDATA/APPDATA。 |

## 三、关键变更分析

### 3.1 win_exec.py +302 行重写

#### (a) 本地落盘日志（`_init_local_log` / `_local_log` / `_push_log` 末尾追加落盘）— win_exec.py:81-187
- **改了什么**：runner 启动时在 jbx-sandbox profile 下开 `jiuwenbox-logs\<sandbox_id>\runner.log`（追加模式，行缓冲），`_push_log` 末尾调 `_local_log` 落盘一份。多路兜底拿 profile 根：env USERPROFILE → `get_sandbox_profile_dir()` → `C:\Users\jbx-sandbox`。
- **为何**：CREATE_NO_WINDOW 下 runner stderr 无落盘，control_port 长连回传链断/卡死时无任何过程日志可查（早期 `_create_restricted_token` 失败会静默退出，box-server 端只看到 ECONNREFUSED）。
- **评价**：🟢 设计合理，best-effort 不抛错，失败降级为只回传不阻断主流程。`flush()` 保证不丢日志。解决了"链路终点无观测"的核心痛点。注释清晰，兜底层级合理。
- **小问题**：`_local_log_file` 是模块级全局单例，多个沙箱的 runner 是**独立进程**（每个 runner 一个 `python -m`），不会共享该全局，无并发问题；但若单 runner 内 `_push_log` 被多 worker 线程并发调，`_local_log_lock` 已保护，OK。

#### (b) `get_sandbox_profile_dir()`（userenv.dll GetUserProfileDirectoryW）— win_exec.py:788-835
- **改了什么**：通过 runner primary token 调 `GetUserProfileDirectoryW` 拿 jbx-sandbox 真实 profile 目录，token 用完即关。
- **为何**：同名残留 profile 会建 `.000` 后缀，路径不固定，hardcode `C:\Users\jbx-sandbox` 不稳。API 拿真实路径才可靠。
- **评价**：🟢 正确处理 size 探测两次调用范式（先 NULL buf 拿 size，再分配 buf 填充）。`finally` 关 token 防泄漏。`except OSError` 降级返回 None 调用方回落旧行为。userenv.dll 延迟加载签名正确（argtypes/restype 显式声明）。
- **小注意**：`GetUserProfileDirectoryW` 第一次探 size 返回的 size 含末尾 NUL，`create_unicode_buffer(size.value)` 分配 `size` 个 WCHAR（含 NUL），`buf.value` 返回去 NUL 的字符串，正确。

#### (c) child env profile 补全 — win_exec.py:877-920
- **改了什么**：第二跳 `_create_process_as_user` 里，用 `get_sandbox_profile_dir()` 拿 profile，把 `TEMP/TMP` 指向 `<profile>\AppData\Local\Temp\jiuwenbox\<sandbox_id>`，并 `setdefault` 补 `USERPROFILE/LOCALAPPDATA/APPDATA`。拿不到 profile 时回落旧 `workspace/.tmp`。
- **为何**：第二跳 child env 来自 header（agent-core），不含 jbx-sandbox profile 变量（实测 LOCALAPPDATA/USERPROFILE/APPDATA/TEMP 全空），Playwright 仅靠 `os.homedir()` fallback 才装对路径。
- **评价**：🟢 补全合理，`setdefault` 不覆盖调用方已注入值。每沙箱隔离子目录（sandbox_id 取自 workspace 末段）删沙箱不串扰，ms-playwright 仍共用跨沙箱复用。降级路径保留旧行为，回归风险低。
- **一致性风险**：🟡 此处 `_sandbox_id = os.path.basename(workspace.rstrip("\\/"))`（workspace 末段），而 process.py 第一跳用的是真实 `sandbox_id`（`_sandbox_sub = sandbox_id`）。正常情况下 workspace 末段 == sandbox_id，但**若 workspace 路径末段不等于 sandbox_id，两跳 TEMP 目录不一致**，child 拿到的是第二跳注入的（basename 版），第一跳建的目录（真实 id 版）未被使用。功能不影响（都指向 jbx-sandbox profile 下可写区），但目录命名不一致、清理可能残留。建议第二跳也接收真实 `sandbox_id` 而非从 workspace 推导。

#### (d) exec 子进程 stdout 读取重写（wait + drain 并行）— win_exec.py:1331-1426
- **改了什么**：旧逻辑先 `WaitForSingleObject` 循环 wait 进程退出，再读 stdout（强杀后用 PeekNamedPipe 轮询 + 总期限读）。新逻辑：起后台 daemon 线程持续 `os.read` drain pipe（写多少读多少，pipe 不满），主线程同时 wait 进程；进程退出后 `join(timeout=5)` 等 drain 结束拿完整 stdout；若 drain 仍活着（孙进程持写端不 EOF）则 `os.close(read_fd)` 强制 drain 退出。
- **为何**：旧逻辑致命缺陷——child（如 `npx playwright install`）写大量进度到 64KB pipe 写满后阻塞在 write 等 reader；但 runner 在 wait 不读 → child 永不退出 → runner 永远等不到 → 死锁卡满 timeout 强杀。且强杀后孙进程持写端 pipe 不 EOF，旧版 PeekNamedPipe 轮询 5s 超时后跳出让 runner accept 循环恢复——但实测 `npx playwright install` 超时强杀后整个 sandbox 后续 bash 全 timed out + WinError 10053，说明旧版恢复并不彻底。新版并行 drain 根治死锁。
- **评价**：🟢 核心修复，正确性显著提升。这是本 commit 最重要的修复，解决了"沙箱内大输出命令必死锁"的硬伤。daemon 线程 `os.read` 阻塞读 + EOF 自然退出范式正确；进程退出后 join 5s 兜底孙进程持写端场景；`os.close(read_fd)` 让 drain 线程的 `os.read` 抛 OSError 退出循环——逻辑自洽。
- **GIL 安全**：🟢 `out_buf.extend(chunk)` 与主线程读 `len(out_buf)` 在 CPython GIL 下 list/bytearray append 原子，心跳日志读 `len(out_buf)` 只读长度安全。
- **截断逻辑回归**：🟡 drain 线程里 `if len(out_buf) > MAX_STDOUT_BYTES: del out_buf[MAX_STDOUT_BYTES:]`。旧版非线程版是 `out_buf = out_buf[:MAX_STDOUT_BYTES]`（重新赋值），新版用 `del` 原地截断（因为 out_buf 是闭包捕获的 bytearray，重新赋值在子线程里不会反映到主线程的 out_buf 引用）。`del` 原地修改是对的，但截断后 `break` 停止读——此时 child 继续写但 pipe 满了 drain 不读，**child 又会阻塞在 write 等读**，但由于主线程已 break 出 wait 循环后 join drain，drain 已退出，child 仍可能阻塞直到 TerminateProcess 强杀。不过这只在 stdout 超过 MAX_STDOUT_BYTES（通常 MB 级）时发生，可接受。

#### (e) exec 心跳与启停日志 — win_exec.py:1313-1315, 1393-1398, 1434-1436
- **改了什么**：exec child 启动/结束各打一条 INFO（pid/cmd/exit_code/killed/stdout_len），等待中每 30s 心跳（waited/budget/buf 大小）。
- **评价**：🟢 运维收益高，定位"卡哪一步"极有用。`command[:3]` 截断防日志爆炸。

### 3.2 win_wfp.py +55 行

#### (a) Permit filter 改为 loopback 全端口放行 — win_wfp.py:756-791
- **改了什么**：旧版逐端口装 Permit（`user + loopback + port_eq`，port 在 60080-60089 范围，每端口一个 uuid5 派生 key）。新版去掉 port 条件，只装 `user + loopback` 全端口放行，key 用固定 base_key（合法 UUID）。
- **为何**：pptx-craft convert 的 render server 用 `getPort()` 随机选端口（如 127.0.0.1:6298），不在固定范围，逐端口 Permit 无法覆盖 → Block → `ERR_NETWORK_ACCESS_DENIED`。
- **评价**：🟡 **隔离影响需重点审视（见四.1）**。代码注释明确标注"此为验证根因的临时全放开; 定稿方案待定"——**这是临时方案，未定稿，技术债**。
- **卸载兼容**：🟢 uninstall 侧既删旧 per-port 残留（uuid5 派生）又删固定 base_key，兼容历史安装，幂等。`_delete_filter_by_key` 忽略 not-found 码（0x80320031/0x80320003），安全。

#### (b) 卸载兼容删旧 per-port 残留 — win_wfp.py:823-835
- **评价**：🟢 兼容旧版每端口一个 filter 的卸载，避免升级后残留 filter。

### 3.3 process.py +28 行

#### TEMP 注入指向 profile + profile 变量补全 — process.py:2968-3005
- **改了什么**：第一跳 runner env 的 TEMP/TMP 从 `workspace/.tmp` 改为 `<profile>\AppData\Local\Temp\jiuwenbox\<sandbox_id>`，补 USERPROFILE/LOCALAPPDATA/APPDATA。profile 根优先 env USERPROFILE，否则 `C:\Users\jbx-sandbox`。
- **为何**：受限 token 写不了宿主 %TEMP%，且第二跳 child env 缺 profile 变量；第一跳就注入好让 runner 继承。
- **评价**：🟢 与 win_exec 第二跳逻辑对齐，双重保险。注释说明了"第一跳 token 还没拿到无法 API 解析，用 env 已注入的 USERPROFILE 或标准名"——合理（第一跳时 runner 进程还没起来，无法用 runner token 调 GetUserProfileDirectoryW）。
- **与 win_exec 的一致性**：🟡 两处都用 `setdefault` 补 profile 变量，但 process.py 用真实 `sandbox_id`，win_exec 用 `workspace` 末段。如 3.1(c) 所述，正常情况一致，异常情况命名残留。建议统一。

## 四、关键代码检视

### 4.1 WFP loopback 全端口放行是否削弱隔离？🟡 中（设计层面）

**当前 WFP filter 状态**（install 后）：
- Block filter（weight=0x0 最低）：`ALE_USER_ID == jbx-sandbox SID` → Block 所有出站连接。
- Permit filter（weight=0xF 最高，覆盖 Block）：`ALE_USER_ID == jbx-sandbox SID` **且** `IP_REMOTE_ADDRESS == 127.0.0.1/::1` → Permit。

**关键结论**：
- 🟢 **非 loopback 出站流量仍被 Block 拦截**。沙箱用户访问任意公网 IP（如 8.8.8.8）命中 Block（weight 低但无更高 weight 的 Permit 覆盖）→ 被拦。**出网唯一出口仍是 win_proxy（127.0.0.1:port_range）**，proxy 转发后由 win_proxy 控制白名单。隔离前提未被破坏。
- 🟡 **但 loopback 全端口放行扩大了沙箱内攻击面**：jbx-sandbox 现在可连**宿主机任意 127.0.0.1 服务**（不只 win_proxy 端口范围）。若宿主机上有其他 loopback 服务（数据库/调试端口/其他代理/其他沙箱的 win_proxy 端口），沙箱内进程可访问之。原设计用 port 条件精确限定到 proxy 端口范围，是更严格的"最小放行"。本次为打通 pptx-craft 临时放开。
- 🟡 **跨沙箱串扰风险**：多个沙箱共享同一宿主机的 loopback 命名空间。沙箱 A 的进程可连沙箱 B 的 control_port（127.0.0.1:<B 的端口>），理论上可发 IPC 帧干扰 B 的 runner。旧版 port 范围限定下此风险被 port 条件挡住（control_port 不在 permit 范围）。新版全端口放行后此保护消失。control_port 协议虽无认证，但 runner 的 `_handle_exec_request` 不校验来源——**任意同机 jbx-sandbox 进程可向任意 runner 发 exec 请求**。

**结论**：核心出网隔离（非 loopback Block）仍有效，但 loopback 侧的"最小权限"被削弱。注释自承"临时方案待定"，属已知技术债。

### 4.2 drain 线程 fd 关闭竞态 — win_exec.py:1407-1424

- `os.close(read_fd)` 后，drain 线程仍在 `os.read(read_fd, ...)` 阻塞中。close 会让其 `os.read` 抛 `OSError`（EBADF），被 `except OSError: break` 捕获 → 循环退出。**逻辑正确**。
- 但 `read_fd` 是 `msvcrt.open_osfhandle(child_out_read.value, ...)` 转的 C runtime fd。close fd 后底层 handle 的归属：`open_osfhandle` 默认（无 `O_NOINHERIT`）会让 close 同时关底层 handle。后续无对 `child_out_read` handle 的 CloseHandle（已由 osfhandle close 接管）。**正确**，无双重释放。
- 旧版分支用 `os.fdopen` 包装 fd，本次删除了 fdopen 分支，统一用裸 `os.read`，简化了 fd 生命周期。🟢

### 4.3 受限 token 现状 — 是否仍受限？🟡 中

`_handle_exec_request` 实际用的是 `_get_runner_primary_token()`（runner 自身未受限 token），而非 `_create_restricted_token()` 创建的受限 token（win_exec.py:1300-1312）。注释（760-769 行）明确：受限 token 让任何 child 启动即 `0xC0000142`（DllMain 失败），故改用 runner 自身未受限 token。

- **现状**：`_create_restricted_token()` 仍在 `runner_main` 启动时调用（1074 行，token handle 保留到退出才关），但**exec 实际未使用它**。第二重写检查（Write-Restricted）名存实亡。
- **隔离降级影响**：写控制只剩合成 SID 的 ACL（allow-only 仍挡越权写）。注释自承"安全降一重，但让 bash/cmd/python 能跑起来"。
- **这是此前 commit 073d4c1e 的决策**（非受限 token 启动），本 commit 未改变此状态。`_create_restricted_token` 的代码留着但实际是 dead code（创建后只在 finally 关，从不用于 exec）。

### 4.4 Job Object 现状 — 仍禁用 🟡 中

process.py:3065-3099 明确禁用 Job Object 资源限制（assign 跨用户 OpenProcess 拿不到 PROCESS_SET_QUOTA → WinError 5）。注释完整保留 resume 逻辑，Job 创建全注释掉。

- **影响**：沙箱无内存/CPU/进程数上限，资源限制缺失。隔离核心（文件 ACL + WFP）不依赖 Job，但**无资源管控 = 一个失控沙箱可耗尽宿主机资源**。
- 这是跨用户 Job assign 的 Windows 限制，非本 commit 引入，但作为"全链路 ok"的收尾，**资源限制仍是缺口**。

### 4.5 密码现状 — 仍硬编码读取 🟢 低

`win_setup.get_sandbox_user_password()` 从注册表读（非源码硬编码），`two_hop_spawn` 接收 password 参数。链路 5f841f7a 的"密码硬编码"问题早已在后续 commit 修掉，本 commit 无回退。

## 五、优点

1. **根治 exec stdout 死锁**：wait + drain 并行的修复方向完全正确，解决了"大输出 child 必死锁 + 强杀后卡死整 sandbox"的硬伤。这是全链路打通最关键的修复，注释把根因（pipe 64KB 写满互锁）和验证方法（重定向到文件正常退出）写得极清晰。
2. **本地落盘日志**：control_port 回传链脆弱时仍有完整过程日志，显著提升可观测性。best-effort 不阻断主流程的设计正确。
3. **profile 变量补全**：第二跳 child env 缺 profile 变量是隐蔽问题，两处（第一跳 process.py + 第二跳 win_exec）双重补全，降级回落旧行为。
4. **卸载兼容性**：WFP 卸载既删旧 per-port 残留又删固定 key，升级幂等无残留。
5. **注释质量极高**：几乎每处改动都有详尽根因说明（含实测现象、验证方法、设计权衡），对后续维护极友好。
6. **心跳日志**：exec 等待中每 30s 心跳，定位"卡哪一步"极有用。

## 六、问题与风险

按严重程度排序：

### P1 🔴 WFP loopback 全端口放行是临时方案，削弱最小权限且引入跨沙箱串扰风险
- win_wfp.py:756-791 去掉 port 条件，放行 jbx-sandbox 访问任意 127.0.0.1 端口。
- 影响：(1) 沙箱内进程可连宿主机任意 loopback 服务（不只 proxy 端口）；(2) 跨沙箱串扰——沙箱 A 可向沙箱 B 的 control_port 发 exec IPC 帧（runner 无来源认证），干扰 B 的 runner。
- 注释自承"临时验证全放开; 定稿方案待定"，属已知技术债，但作为"全链路 ok"收尾仍带此债上线需评估。
- **非 loopback 出网隔离仍有效**（Block 仍挡公网）。

### P2 🟡 受限 token 实际未使用，Write-Restricted 双重写检查名存实亡
- win_exec.py:1300-1312 exec 用 `_get_runner_primary_token()`（未受限），`_create_restricted_token()` 创建的 token 从不用于 exec（只在启动时创建+退出时关闭）。
- 写控制只剩合成 SID ACL（allow-only 挡越权写），安全降一重。
- 这是 073d4c1e 的决策（受限 token 让 child 0xC0000142），本 commit 未改变。`_create_restricted_token` 相关代码实际是 dead code（除 finally CloseHandle 外无引用）。

### P3 🟡 Job Object 资源限制仍禁用
- process.py:3065-3099 注释禁用 Job（跨用户 assign 拿不到 PROCESS_SET_QUOTA）。
- 沙箱无内存/CPU/进程数上限，失控沙箱可耗尽宿主机资源。隔离不依赖 Job，但资源管控缺口。

### P4 🟡 TEMP 子目录命名两跳不一致
- win_exec.py:889 用 `os.path.basename(workspace.rstrip("\\/"))`（workspace 末段），process.py:2978 用真实 `sandbox_id`。
- 正常一致，异常（workspace 末段 != sandbox_id）时两跳 TEMP 目录不同，第一跳建的目录未用、清理可能残留。

### P5 🟢 drain 线程 stdout 超 MAX_STDOUT_BYTES 截断后 child 可能再次阻塞
- win_exec.py:1363-1365 截断后 `break` 停止读，child 继续写满 pipe 阻塞到 TerminateProcess 强杀。
- 仅在 stdout 超 MB 级时发生，可接受。可考虑截断后不 break 继续丢弃式读（read 后不 extend）让 child 不阻塞。

### P6 🟢 工作树遗留临时调试文件（未纳入 commit，但环境不洁）
- 仓库根有未跟踪：`4088.diff`、`interface.txt`、`docs/windows_sandbox_debug_progress.md`、`jiuwenbox/box-install.txt`、`jiuwenbox/failed.txt`、`jiuwenbox/install_run.log`、`jiuwenbox/uninstall.log`、`jiuwenbox/uninstall.out`、`jiuwenbox/src/jiuwenbox/install_force.log`。
- 本 commit 未引入这些（git status 显示它们是 untracked），但工作树不洁，建议清理或 .gitignore。

## 七、改进建议

1. **P1 定稿 WFP loopback 方案**：
   - 方案 A（推荐）：改 pptx-craft render server 用固定端口（从 proxy port_range 预留一段给本地 render server），恢复 port 条件 Permit。
   - 方案 B：沙箱侧动态 Permit render server 实际选用的端口（需监听 child 进程的 socket 绑定事件，复杂）。
   - 至少在 runner 端对 control_port 连接做来源校验（如仅接受 box-server 进程的连接，或加握手 token），缓解跨沙箱串扰。
2. **P2 清理 dead code**：`_create_restricted_token` 若确认弃用，删除其调用与相关 _SID_AND_ATTRIBUTES 等结构，避免误导维护者以为还在用受限 token。或若计划恢复，加 `# TODO` 标注。
3. **P3 Job Object**：若短期无法解决跨用户 assign，至少加 per-sandbox 内存软限（如用 JobObject 的 JS_LIMIT_COMMIT，需 PROCESS_SET_QUOTA）；或文档明确标注"Windows 沙箱无资源限制，依赖宿主机资源监控"。
4. **P4 统一 sandbox_id 来源**：`_handle_exec_request` 与 `_create_process_as_user` 已有 `workspace` 参数，补传真实 `sandbox_id`（从 header 或 runner_main args 拿），避免 basename 推导。
5. **P5 截断后继续丢弃式读**：`if len(out_buf) > MAX_STDOUT_BYTES` 后改为 `continue`（read 但不 extend），让 child 不阻塞，直到自然 EOF 或 timeout。
6. **P6 清理工作树**：删除或 .gitignore 上述未跟踪调试文件。

## 八、小结（含整体链路成熟度评价）

### 本 commit 的链路收尾价值
本 commit 作为 Windows 沙箱适配链路的终点，修复了"最后一公里"的三个硬伤：exec stdout 死锁、profile 变量缺失、WFP loopback 随机端口被 Block。其中 **exec stdout 死锁修复是全链路最关键的修复**——它直接决定了"沙箱内跑大输出命令是否可用"，是 bash/playwright install 等核心场景能跑通的前提。本地落盘日志补齐了可观测性。修复方向正确、回归风险低（降级保留旧行为）。

### 结合 20 个 commit 演进的整体成熟度评价

从基线 `5f841f7a feat:window 沙箱` 到本 commit 的演进脉络：

| 维度 | 基线 5f841f7a | 当前 82001d09 | 状态 |
| --- | --- | --- | --- |
| **受限 token** | 弃用受限 token（让 child 0xC0000142） | 仍未用受限 token，exec 用 runner primary token | 🟡 Write-Restricted 双重写检查名存实亡，只剩 ACL 单重写控 |
| **Job Object** | 禁用 Job（跨用户 assign WinError 5） | 仍禁用 | 🟡 无资源限制 |
| **密码** | 硬编码 | 从注册表读（`get_sandbox_user_password`） | 🟢 已修 |
| **WFP** | 逐端口 Permit + Block | loopback 全端口 Permit + Block | 🟡 loopback 侧最小权限削弱（临时方案），非 loopback 出网隔离仍有效 |
| **ACL** | 合成 SID ACL | 合成 SID ACL + apply_sandbox_acl 对 allow_read 给真实 SID grant | 🟢 持续加固 |
| **exec 链路** | pipe 串行 wait-then-read 死锁 | wait+drain 并行 | 🟢 本 commit 根治 |
| **可观测性** | stderr 无落盘，control_port 断即无日志 | 本地落盘 runner.log + 心跳 + 启停日志 | 🟢 本 commit 补齐 |
| **profile/环境** | TEMP 指 workspace/.tmp | TEMP 指 profile 隔离子目录 + profile 变量补全 | 🟢 本 commit 修 |
| **relay-claw 配置** | 无 | f4089537 支持 relay-claw 配置沙箱启停/黑白名单 | 🟢 已接入 |

### 残余风险与生产就绪判断

**残余风险**：
1. WFP loopback 全端口放行（临时方案，跨沙箱串扰 + loopback 最小权限削弱）。
2. 受限 token 实际未用（写控制单重 ACL）。
3. Job 资源限制缺失（无内存/CPU 上限）。
4. control_port IPC 无来源认证（依赖 WFP 的 user 条件隐式隔离，但 loopback 全放开后此隐式隔离被削弱）。

**成熟度评价**：当前 Windows 沙箱**功能链路已打通**（创建/启动/exec/文件操作/联网代理/relay-claw 配置全可用），核心隔离（文件 ACL + WFP 非 loopback 出网 Block + 合成 SID 写控）仍有效，可观测性已补齐。但**隔离强度从设计初预期的"三重（ACL + 受限 token + WFP 精确端口）"降级为"双重（ACL + WFP loopback 全放）"**，且 WFP 放开是临时方案。

**生产就绪**：
- **受限场景可用**：单租户/可信用户、对 loopback 侧串扰不敏感、有宿主机资源监控兜底的部署，当前状态可灰度上线。
- **生产就绪前置条件**（建议解决后再全量）：
  1. 定稿 WFP loopback 方案（恢复 port 精确限定或加 control_port 认证）。
  2. 清理 `_create_restricted_token` dead code 或恢复受限 token（需解决 0xC0000142）。
  3. Job 资源限制或替代资源管控。
- 整体评价：**功能就绪（链路 ok），隔离与资源管控待加固**。作为"全链路 ok"的收尾 commit，它诚实地完成了"打通"的使命，技术债也明确标注在注释中，未掩盖问题。
