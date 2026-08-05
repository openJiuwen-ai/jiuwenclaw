# Windows 沙箱适配链路检视报告总览

本目录收录从 `5f841f7a`（feat:window 沙箱基线）到 `82001d09`（fix:全链路ok）共 **21 个 commit** 的逐 commit 检视报告，按时间**从远到近**排列。每份报告基于该 commit 的真实 diff 与相关源码逐行审查，引用 `file:line`，按 🔴高/🟡中/🟢低 标注严重程度。

## 报告索引（从远到近）

| # | Commit | 日期 | 说明 | 报告 |
|---|--------|------|------|------|
| 1 | 5f841f7a | 2026-07-21 | feat:window 沙箱（基线，+4575行） | [5f841f7a_feat-window-sandbox.md](5f841f7a_feat-window-sandbox.md) |
| 2 | c2c3f5f0 | 2026-07-21 | fix:review（首轮 review 修复） | [c2c3f5f0_fix-review.md](c2c3f5f0_fix-review.md) |
| 3 | bb1afca0 | 2026-07-22 | review2（二轮 review） | [bb1afca0_review2.md](bb1afca0_review2.md) |
| 4 | f52aa505 | 2026-07-24 | fix:接入relay-claw | [f52aa505_fix-relay-claw.md](f52aa505_fix-relay-claw.md) |
| 5 | 21024d62 | 2026-07-24 | fix:增加调试日志 | [21024d62_fix-debug-log.md](21024d62_fix-debug-log.md) |
| 6 | fa85c987 | 2026-07-25 | fix:修启动风格 | [fa85c987_fix-startup-style.md](fa85c987_fix-startup-style.md) |
| 7 | 3fe33056 | 2026-07-25 | fix:修复沙箱用户创建bug | [3fe33056_fix-sandbox-user-create.md](3fe33056_fix-sandbox-user-create.md) |
| 8 | fb587eac | 2026-07-25 | fix:沙箱启动报错 | [fb587eac_fix-sandbox-startup-error.md](fb587eac_fix-sandbox-startup-error.md) |
| 9 | ab4932ac | 2026-07-27 | fix:修box-server启动报错 | [ab4932ac_fix-box-server-startup.md](ab4932ac_fix-box-server-startup.md) |
| 10 | d15fcf8e | 2026-07-27 | fix:修box启动（+设计文档） | [d15fcf8e_fix-box-startup.md](d15fcf8e_fix-box-startup.md) |
| 11 | e8fb0a69 | 2026-07-27 | fix:修复沙箱创建问题（WFP ctypes） | [e8fb0a69_fix-sandbox-create.md](e8fb0a69_fix-sandbox-create.md) |
| 12 | 8c7f677a | 2026-07-28 | fix:修权限... | [8c7f677a_fix-permission.md](8c7f677a_fix-permission.md) |
| 13 | ee03da56 | 2026-07-28 | fix:删除sand-box用户 | [ee03da56_fix-delete-sandbox-user.md](ee03da56_fix-delete-sandbox-user.md) |
| 14 | 073d4c1e | 2026-07-29 | fix:bash工具沙箱内执行成功，非受限token启动 | [073d4c1e_fix-bash-exec-unrestricted-token.md](073d4c1e_fix-bash-exec-unrestricted-token.md) |
| 15 | 432a5001 | 2026-07-30 | fix:修沙箱内联网 | [432a5001_fix-sandbox-network.md](432a5001_fix-sandbox-network.md) |
| 16 | 2d19941c | 2026-07-30 | fix:解决沙箱内网络访问问题 | [2d19941c_fix-sandbox-network-access.md](2d19941c_fix-sandbox-network-access.md) |
| 17 | a3d0d2bd | 2026-07-30 | fix:对接前端relay-claw接口（设计文档） | [a3d0d2bd_fix-relay-claw-frontend-api.md](a3d0d2bd_fix-relay-claw-frontend-api.md) |
| 18 | f4089537 | 2026-07-30 | fix:支持relay-claw配置 | [f4089537_fix-relay-claw-config.md](f4089537_fix-relay-claw-config.md) |
| 19 | 7fe80192 | 2026-07-31 | fix:修启动时读取策略副本 | [7fe80192_fix-policy-replica-read.md](7fe80192_fix-policy-replica-read.md) |
| 20 | cafaa1f1 | 2026-07-31 | fix:自动探测python | [cafaa1f1_fix-autodetect-python.md](cafaa1f1_fix-autodetect-python.md) |
| 21 | 82001d09 | 2026-08-01 | fix:全链路ok（收尾） | [82001d09_fix-fullchain-ok.md](82001d09_fix-fullchain-ok.md) |

## 跨 commit 的系统性主线（按主题）

> 以下为审查中浮现的、贯穿多个 commit 的核心问题脉络，便于横向把握整条适配链路的成熟度。

### A. 受限 token（Write-Restricted）从设计到弃用
- 基线 [5f841f7a](5f841f7a_feat-window-sandbox.md)：`_create_restricted_token` 已实现，但 exec 实际用 runner 未受限 primary token，双重写检查名存实亡。
- [d15fcf8e](d15fcf8e_fix-box-startup.md)：ctypes 结构体对齐修复（`_SID_AND_ATTRIBUTES`/`_TOKEN_GROUPS` 8 字节对齐、PSID 指针签名、悬垂指针）。
- [fb587eac](fb587eac_fix-sandbox-startup-error.md)：受限 token 路径仍不可用（`0xC0000142 STATUS_DLL_INIT_FAILED`）。
- [073d4c1e](073d4c1e_fix-bash-exec-unrestricted-token.md)：正式弃用受限 token，exec 改用非受限 primary token，安全降一重。
- [8c7f677a](8c7f677a_fix-permission.md) / [82001d09](82001d09_fix-fullchain-ok.md)：受限 token 沦为 dead code，写控制只剩合成 SID ACL 单重。

### B. WFP 网络过滤的演进与临时放开
- [5f841f7a](5f841f7a_feat-window-sandbox.md)：Permit 放行所有 loopback 端口，win_proxy 未启动。
- [e8fb0a69](e8fb0a69_fix-sandbox-create.md)：修复 WFP ctypes 隐蔽错误（union 大小 16B、GUID 错值、字段拼写 `filterConditions`、V4/V6 分支误判），常量错值集中修正。
- [432a5001](432a5001_fix-sandbox-network.md)：网络白名单三层模型（WFP Block + Permit loopback:port_range + win_proxy）确立；真根因是 bash 路径反斜杠被吞。
- [82001d09](82001d09_fix-fullchain-ok.md)：为适配 render server 随机端口，loopback 再次去 port 条件全放行（临时债，跨沙箱串扰风险）。

### C. ACL 与文件隔离
- [c2c3f5f0](c2c3f5f0_fix-review.md)：ACL 修复在本 commit 实际不生效（`GetAclSize()` 当 ACE 数、`_parse_getace_tuple` 子元组不兼容）。
- [3fe33056](3fe33056_fix-sandbox-user-create.md)：`KEY_READ_WRITE` 权限位 0x20019→0x2001F、`NetLocalGroupAdd` argtypes 错位修复。
- [432a5001](432a5001_fix-sandbox-network.md)：ACE 继承标志 0x7→0x3 修复（影响所有递归 grant）。
- [8c7f677a](8c7f677a_fix-permission.md) / [432a5001](432a5001_fix-sandbox-network.md)：`~/.office-claw` 整树递归 grant Read+Write 给真实 SID，deny_write 对真实 SID 无 Deny ACE → 跨沙箱 workspace 互写、deny_write 被绕过。

### D. jbx-sandbox 用户与密码
- [5f841f7a](5f841f7a_feat-window-sandbox.md) 起：密码硬编码 `"000000"`，`SANDBOX_USER_PASSWORD_LENGTH=64` 常量定义未用。
- [3fe33056](3fe33056_fix-sandbox-user-create.md)：`_reg_get_str` 两阶段读修复 DPAPI 密码 hex 截断（WinError 1326 根因）。
- [ee03da56](ee03da56_fix-delete-sandbox-user.md)：用 `DeleteProfileW` + `NetUserDel` 消解 reinstall 密码不一致；但 uninstall 删用户前未确认 runner 退出。
- 全链路终点 [82001d09](82001d09_fix-fullchain-ok.md)：密码仍是固定弱口令，生产前必须随机化。

### E. relay-claw 配置链路
- [f52aa505](f52aa505_fix-relay-claw.md)：runner 子进程 env 全量透传凭据、`os.environ` 全局污染、强杀致孤儿沙箱。
- [a3d0d2bd](a3d0d2bd_fix-relay-claw-frontend-api.md)：设计文档质量高且与实现对应，但接口数 6 vs 8 口径不一、`enabled` 默认值文档与代码矛盾、安全考量缺失。
- [f4089537](f4089537_fix-relay-claw-config.md)：网络 `default` 由 deny 改 allow 致隔离形同虚设、黑白名单无输入校验、WS 仅 Origin 校验无用户级鉴权。
- [7fe80192](7fe80192_fix-policy-replica-read.md)：副本落沙箱可写路径可被篡改自放宽隔离、并发写入无锁 lost-update。

### F. 进程执行与可观测性
- [fb587eac](fb587eac_fix-sandbox-startup-error.md)：pipe→TCP loopback 重构；但 runner env 丢失 PATH、exec 2s 超时回归。
- [21024d62](21024d62_fix-debug-log.md)：正式可观测性增强（非临时调试遗留），但 `exec_in_sandbox` debug 日志含完整命令行可能泄露凭据。
- [82001d09](82001d09_fix-fullchain-ok.md)：exec stdout 死锁根治（后台 drain 线程 + join 5s 兜底），全链路打通最关键修复；新增本地落盘日志补齐可观测性。

### G. Python 探测
- [ab4932ac](ab4932ac_fix-box-server-startup.md)：runner python 选 venv 方案属中间态伪修复（uv trampoline 仍 WinError 5），后续回退。
- [d15fcf8e](d15fcf8e_fix-box-startup.md)：CPython 探测路径硬编码 dev 机（`D:\Files\python313\python.exe`）。
- [cafaa1f1](cafaa1f1_fix-autodetect-python.md)：自动探测方向正确，但 install 提权路径绕过探测致 ACL 预装与运行时不一致。

## 全链路成熟度小结

功能链路在 `82001d09` 已**打通**（exec stdout 死锁根治为关键拐点），核心隔离（合成 SID ACL + WFP 非 loopback Block）仍有效，可观测性已补齐（本地 runner.log）。但隔离强度从设计预期的"三重"（受限 token + ACL + WFP）**降级为"双重"**（ACL + WFP），且存在多处**临时债**与**生产阻断项**：

1. 🔴 WFP loopback 全端口放行（临时方案，跨沙箱串扰 + 无认证 IPC）——需定稿。
2. 🔴 受限 token dead code，双重写检查名存实亡——需恢复或显式标注降级。
3. 🔴 `~/.office-claw` 整树递归 grant Write + deny_write 对真实 SID 无 Deny——跨沙箱互写。
4. 🔴 jbx-sandbox 密码固定 `"000000"`——本地提权面。
5. 🔴 relay-claw 配置链路：网络 default=allow、副本落沙箱可写路径、WS 无用户级鉴权。
6. 🟡 Job Object 资源限制禁用——fork bomb/内存炸弹可耗尽宿主。
7. 🟡 install 等待 `INFINITE` 无超时——install 子进程崩溃可永久阻塞。

**结论：灰度可用，全量生产前需先清上述 1-5 项。** 每项的详细定位见对应报告。
