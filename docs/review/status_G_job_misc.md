# 状态核对 G：Job 资源限制与杂项

核对基线：HEAD `82001d09` (当前工作区状态)
核对人：资深 Windows 系统工程审查员
核对日期：2026-08-01

| # | 问题 | 报告出处 | 当前状态 | 证据 file:line | 说明 |
|---|------|----------|----------|----------------|------|
| 1 | Job Object 禁用，跨用户 assign 失败致 memory_max/cpu_rate/max_processes 不生效，沙箱无资源约束 | 5f841f7a §4.5/六#7 | ❌仍存在 | `process.py:3065-3111`；`win_job.py:131-211` | Job 创建/assign 整段注释禁用。`process.py:3068-3075` 注释明写"本版本禁用 Job Object 资源限制"。仅保留 `win_job.resume_process(thread_handle)`（line 3113）唤醒 CREATE_SUSPENDED runner。policy 的 `resource.memory_max/cpu_rate/max_processes` 配置保留但在运行时被忽略。`win_job.py` 模块本身完整（create_job/assign_process/close_job 均实现），只是 `_create_windows` 不调用。沙箱无内存/CPU/进程数上限，fork bomb/内存炸弹可耗尽宿主。 |
| 2 | Job Object 资源限制被整段注释禁用（process.py 约 3031-3081），注释给出恢复方向（用 two_hop_spawn 返回的 proc_handle 直接 assign 绕过跨用户 OpenProcess）但未实施 | 073d4c1e §3.3 | ❌仍存在 | `process.py:3068-3111`（注释块）；`process.py:3060`（`proc_handle` 已存入 `_win_runners`） | 仍注释禁用，proc_handle 直接 assign 方案**未实施**。注释（`process.py:3074-3075`）明确"后续若需资源限制, 改回: 用 two_hop_spawn 返回的 proc_handle 直接 assign (而非 pid OpenProcess) 以绕过跨用户 ACL"——方向已记录但代码未落地。注意：`win_job.assign_process(job_handle, process_handle)` 已支持 handle 直接 assign（`win_job.py:203-211`），`win_job.assign_process_by_pid`（line 214-229）才是走 pid OpenProcess 的跨用户路径。恢复只差把 `proc_handle` 传给 `assign_process` 而非 `assign_process_by_pid`，但当前无人调用。 |
| 3 | 82001d09 报告未提 Job 恢复，预期仍禁用 | 82001d09 §4.4/P3 | ❌仍存在（符合预期） | `process.py:3065-3111` | 82001d09 报告 §4.4 确认"Job Object 现状 — 仍禁用"，P3 列为已知技术债。当前 HEAD 状态与报告一致，Job 仍禁用。注释/代码未改动。 |
| 4 | 用约定键 JIUWENBOX_INJECT_ENV（JSON dict）注入工具私有 env 键，pop 删约定键防泄漏，setdefault 注入保证 agent-core 不能覆盖沙箱强制代理指向 | 2d19941c §改动分析 | ✅已解决 | `win_exec.py:938-959` | 机制完整保留：`env.pop("JIUWENBOX_INJECT_ENV", None)`（line 938）解析后删约定键防泄漏；`json.loads` + `isinstance(_injected, dict)` 校验（line 942-944）；解析失败只 warning 不阻断（line 945-950）；`env.setdefault(str(_k), str(_v))`（line 954）保证不可覆盖已注入的 HTTP_PROXY 等；注入键名列表记 INFO 日志（line 956-958）。与 2d19941c 报告描述完全一致，机制未变。 |
| 5 | 注入键无黑名单，可注入 LD_PRELOAD/PYTHONPATH/NODE_OPTIONS/PATH 等代码注入类键 | 2d19941c §问题表/建议1 | ❌仍存在 | `win_exec.py:951-955` | 仍无禁用键黑名单。全仓 grep `LD_PRELOAD\|PYTHONPATH\|NODE_OPTIONS\|forbidden\|blacklist\|FORBIDDEN` 在 `win_exec.py` 内零命中（仅 test.sh/seccomp.py 有无关命中）。循环 `for _k, _v in _injected.items(): env.setdefault(str(_k), str(_v))` 对任意键无过滤，LD_PRELOAD/PYTHONPATH/NODE_OPTIONS/PATH 等代码注入类键可被注入。仅靠 setdefault 不覆盖已有键做间接防护（沙箱已 setdefault PATH/SystemRoot 等），但未被预置的键（如 LD_PRELOAD/NODE_OPTIONS）可被注入。报告建议的 `_FORBIDDEN_INJECT_KEYS` frozenset 未实施。 |
| 6 | 与 agent-core 的协议无版本协商，JSON schema 是隐性约定，schema 变更静默降级且无单测 | 2d19941c §跨仓耦合/建议3,4 | ❌仍存在 | `win_exec.py:938-959` | 仍无 `__v` 版本字段协商。解析失败走 `except (ValueError, TypeError)` 降级为 `_injected=None` + warning（line 945-950），schema 变更（如改数组形式）会被 `isinstance(_injected, dict)` 判否 → raise ValueError → 静默降级不注入，表现为"网络又挂"难定位。无单测覆盖该解析段（全仓未见针对 JIUWENBOX_INJECT_ENV 的 test）。 |
| 7 | process.py 与 win_exec.py 的 TEMP 注入语义不一致（setdefault vs 直接覆盖） | 432a5001 §3.4/R6 | ❌仍存在 | `process.py:2995-2996`（setdefault）；`win_exec.py:901-902, 914-915`（直接赋值） | 不一致持续。第一跳（process.py）：`env.setdefault("TEMP", win_tmp_dir)` / `env.setdefault("TMP", win_tmp_dir)`（line 2995-2996），不覆盖调用方。第二跳（win_exec.py）：`env["TEMP"] = _child_tmp` / `env["TMP"] = _child_tmp`（line 901-902，profile 路径分支；line 914-915，workspace/.tmp 降级分支），直接覆盖调用方。两处注释各自说明理由（runner 侧覆盖是因经 JSON 序列化可能丢值），但语义未统一。82001d09 报告 §3.3 也记录此不一致但未修。 |
| 8 | HTTP_PROXY 指向 127.0.0.1 可信本机（win_exec.py 约 923-930 注入） | 2d19941c §关键代码检视；432a5001 §4.4 | ✅已解决（安全无 SSRF） | `win_exec.py:923-930` | `proxy_url = f"http://127.0.0.1:{_proxy_port_start}"`（line 923），setdefault 注入 HTTP_PROXY/HTTPS_PROXY/http_proxy/https_proxy/ALL_PROXY（line 924-928），`NO_PROXY=127.0.0.1,localhost,::1`（line 930）放行 loopback。代理指向可信本机 loopback，无 SSRF/中间人风险。出网由 WFP Block 兜底拦截（非 loopback 全拦），win_proxy 做域名/IP 白名单过滤。机制未变，安全。 |
| 9 | exec socket（runner_main bind 127.0.0.1:control_port 后 accept() 任意连接直接按 type 分发 exec/write_file/read_file）无 token 握手或身份校验，配合 exec child 用未受限 runner primary token 起进程，构成本机任意进程越权执行风险 | d15fcf8e §6.1/建议1 | ❌仍存在 | `win_exec.py:1093-1173` | runner_main 仍无认证。`listener.bind(("127.0.0.1", port))` + `listener.listen(64)`（line 1096-1097）后 `conn, _ = listener.accept()`（line 1107）直接 `recv_frame` 读 header 按 `type` 分发 exec/write_file/read_file/list_dir/shutdown/subscribe_log（line 1121-1163）。全程无共享密钥/token 握手/连接方身份校验。exec 路径用 `_get_runner_primary_token()`（未受限 runner 自身 token）起 child（line 1303），能连上 control_port 的本机进程可让 runner 以 jbx-sandbox 未受限身份执行任意命令。报告建议的"box-server 分配 control_port 时同时分配随机 token 经命令行参数传 runner，首帧校验"未实施。82001d09 报告 P1 也标注此为残余风险（loopback 全端口放行后跨沙箱串扰面扩大）。 |
| 10 | WFP uninstall 范围不匹配：uninstall 默认范围，install 用 policy 范围，改配置后卸载残留旧 filter（归 WFP 组，但确认 process.py 侧调用是否传实际范围） | c2c3f5f0 §六#6/建议5 | ❌仍存在（process.py 侧不涉及，uninstall 调用点未传范围） | `win_setup.py:1252`（`win_wfp.uninstall_wfp_filters()` 无参调用）；`win_wfp.py:805-807`（默认参数 `DEFAULT_PROXY_PORT_RANGE_START/END`） | `uninstall()` 调 `win_wfp.uninstall_wfp_filters()` 不传参（win_setup.py:1252），用默认端口范围。install 侧 `install_wfp_filters` 接收 `permit_port_start/end`（win_wfp.py:716-717，来自 policy），若 policy 改了端口范围后卸载，仍按默认范围删 → 旧范围 filter 残留。注：当前 win_wfp Permit filter 已改为固定 base_key 全端口放行（win_wfp.py:825-835，见 82001d09），per-port 遍历删旧残留仅兼容历史；故新装环境下"范围不匹配"影响减弱（固定 key 删除不依赖范围），但旧版 per-port 安装的残留仍受范围不匹配影响。process.py 侧无 uninstall 调用（卸载在 win_setup.uninstall 提权子进程），确认无误判。 |
| 11 | win_job.py 初始实现（223行）的质量问题，确认当前 win_job.py 是否有实质性改进或仍为初始版本 | 5f841f7a §4.5 | 🟢已改进（非阻断，质量问题属低危） | `win_job.py:1-250` | 当前 win_job.py 共 250 行（5f841f7a 基线 223 行）。实质性改进：c2c3f5f0 加了 `JOB_OBJECT_LIMIT_JOB_MEMORY` + `JobMemoryLimit` 双保险（win_job.py:152-159，对齐 cgroup memory.max 整 job 上限），加了 `assign_process_by_pid`（line 214-229）和 `resume_process`（line 116-128）配合 CREATE_SUSPENDED SUSPEND→Assign→Resume 设计。结构体定义（IO_COUNTERS/JOBOBJECT_*）与 kernel32 原型签名完整。模块本身质量良好，问题不在 win_job.py 实现而在 process.py 不调用它（见 #1/#2）。 |
| 12 | .exec_failed.log 落盘在 workspace 内可被 child 读（归 WFP/网络组，但确认 win_exec.py 落盘点） | 432a5001 §4.3/R5 | ❌仍存在 | `win_exec.py:1457` | 仍落盘在 workspace 内：`_failed_log = os.path.join(str(workspace), ".exec_failed.log")`（line 1457），`open(_failed_log, "w", ...)` 写入完整 stdout（line 1458-1462）。workspace 是沙箱 child 可读路径（apply_sandbox_acl grant Read），child 后续 exec 可读该日志。若 stdout 含敏感路径/凭据会暴露给沙箱进程。报告建议落盘到 box-server 私有目录（不在 allow_read 内）未实施。 |

## 汇总

本组共核对 **12** 条：
- ✅ 已解决 **2** 条（#4 env 注入机制、#8 HTTP_PROXY 可信本机）
- ❌ 仍存在 **9** 条（#1/#2/#3 Job 禁用、#5 注入键无黑名单、#6 协议无版本协商无单测、#7 TEMP 语义不一致、#9 exec socket 无鉴权、#10 WFP uninstall 范围不匹配、#12 .exec_failed.log 落盘 workspace）
- 🟢 已改进 **1** 条（#11 win_job.py 质量改进，但模块未被调用故实质无效）

## 仍存在/部分解决清单（需后续修复）

### 高优先级（安全相关）
1. **#9 exec socket 无鉴权**（`win_exec.py:1093-1173`）：control_port accept 无 token 握手/身份校验 + exec 用未受限 runner primary token 起进程，本机任意进程可越权执行。建议加 box-server 分配的随机 token 经命令行参数传 runner，首帧校验。
2. **#5 注入键无黑名单**（`win_exec.py:951-955`）：LD_PRELOAD/PYTHONPATH/NODE_OPTIONS 等代码注入类键可经 JIUWENBOX_INJECT_ENV 注入。建议加 `_FORBIDDEN_INJECT_KEYS` frozenset 拒绝。
3. **#1/#2/#3 Job Object 禁用**（`process.py:3065-3111`）：沙箱无内存/CPU/进程数上限。恢复方向已在注释中（用 proc_handle 直接 assign 绕过跨用户 OpenProcess），`win_job.assign_process` 已支持 handle 直接 assign，只差 `_create_windows` 调用接线。

### 中优先级（一致性/可维护性）
4. **#6 协议无版本协商无单测**（`win_exec.py:938-959`）：JIUWENBOX_INJECT_ENV schema 变更静默降级难定位。建议加 `__v` 字段 + 单测。
5. **#7 TEMP 注入语义不一致**（`process.py:2995` setdefault vs `win_exec.py:901` 直接赋值）：建议统一。
6. **#12 .exec_failed.log 落盘 workspace**（`win_exec.py:1457`）：建议落盘 box-server 私有目录。

### 低优先级
7. **#10 WFP uninstall 范围不匹配**（`win_setup.py:1252` 无参调用）：新装环境（固定 key 全端口放行）影响减弱，但旧版 per-port 残留仍受影响。建议 uninstall 接收实际范围或持久化已装范围。
