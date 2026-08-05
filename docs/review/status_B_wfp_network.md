# 状态核对 B：WFP 网络过滤与代理

> 核对基准：工作区 = HEAD `82001d09`（branch `enterprise_dev_windowbox`）
> 核对范围：`win_wfp.py` / `win_proxy.py` / `win_constants.py` / `windows-policy.yaml` / `process.py` / `app.py` / `win_exec.py`
> 图例：✅已解决 / ❌仍存在 / ⚠️部分解决 / 🔄以其他方式绕过

## 一、逐条核对表

| # | 问题 | 报告出处 | 当前状态 | 证据 file:line | 说明 |
|---|------|---------|---------|---------------|------|
| 1 | WFP Permit filter 去掉 port_range 限制，沙箱可访问 127.0.0.1 任意端口绕过 win_proxy，且 Windows 侧无 box-server 端口保护 | 5f841f7a §4.3 / §6.3 | ✅已修复（2026-08-02） | win_wfp.py:756-790 | 已回退 `82001d09` 全端口放行，恢复 per-port Permit（`for port in range(permit_port_start, permit_port_end+1)` + `_build_port_eq_condition(port)`），仅放行 `127.0.0.1:60080-60089`。保留 `82001d09` 引入的 `is_v4` 显式判断修复。uninstall 保留固定 base_key 清理以兼容升级残留。实测端口收窄后 PPT 生成正常。 |
| 2 | win_proxy 未在 _create_windows 中启动，WFP Permit 放行的端口范围上无代理监听，沙箱内走代理出网全 ECONNREFUSED | 5f841f7a §4.8 / §6.4 | 🔄以其他方式绕过 | app.py:326-333；process.py 无 win_proxy 调用 | **未在 `_create_windows` 中启动**（process.py:2827-3099 grep 无 `serve_windows_proxy`），但在 **app.py lifespan**（win32 分支）启动：`_win_proxy_task, _win_proxy_stop = await win_proxy.serve_windows_proxy(...)`，监听 `port_range_start/end`，stop 时 `_win_proxy_stop.set()`（app.py:393-402）。即代理由进程级 lifespan 而非 per-sandbox 启动，端口范围上**有代理监听**，ECONNREFUSED 问题已绕过。但代理是进程级单例（非 per-sandbox 独立代理），多沙箱共享同一代理实例+同一端口范围。 |
| 3 | FWPM_FILTER0 末尾 union 误为 c_uint64(8B) 实应 16B union 导致 RPC_X_BAD_STUB_DATA(0x6F7) | e8fb0a69 §3.1[根因1] | ✅已解决 | win_wfp.py:338-346 | 已引入 `_FWPM_FILTER0_UNION(ctypes.Union)`：`_fields_ = [("rawContext", c_uint64), ("providerContextKey", GUID)]`，UINT64(8B) 与 GUID(16B) 取 max=16B。`FWPM_FILTER0.Anonymous` 字段用此 union（win_wfp.py:362）。注释记录旧 bug sizeof=192 vs SDK 200。 |
| 4a | filterCondition 字段名拼成 filterConditions 致指针 NULL | e8fb0a69 §3.1[根因2] | ✅已解决 | win_wfp.py:672-674 | 当前用 `flt.filterCondition = ctypes.cast(cond_array, ctypes.POINTER(FWPM_FILTER_CONDITION0))`（单数）。注释记录旧 bug "字段名拼错成 filterConditions (带 s), ctypes 当成新实例属性, 结构体内的 filterCondition 字段保持 NULL"。 |
| 4b | FWPM_LAYER_ALE_AUTH_CONNECT_V4 GUID 第4段 900F 错值应为 904F | e8fb0a69 §3.1[根因3] | ✅已解决 | win_constants.py:262 | `FWPM_LAYER_ALE_AUTH_CONNECT_V4 = "C38D57D1-05A7-4C33-904F-7FBCEEE60E82"`，第4段为 `904F`（已修正，旧值 900F）。注释记录"S12 旧 bug: V4 的 GUID 第 4 段 904F 误写成 900F, BFE 返回 FWP_E_LAYER_NOT_FOUND"。 |
| 4c | V4/V6 分支用 "V4" in base_key 判断纯 hex GUID 致 V4 层装 IPv6 条件 | e8fb0a69 §3.1[根因4] | ✅已解决 | win_wfp.py:774 | 改为 `is_v4 = (layer == const.FWPM_LAYER_ALE_AUTH_CONNECT_V4)`，按 layer GUID 显式判断。注释记录旧 bug "旧代码两路都走 else → V4 层装了 IPv6 ::1 条件 (FWP_V6_ADDR_AND_MASK=257), BFE 因 condition 类型与 layer 不匹配返回 0x80320027"。 |
| 5 | 临时安全降级——Permit filter 去掉 port 条件、改用固定 base_key，jbx-sandbox 可访问 127.0.0.1 任意端口 | e8fb0a69 §3.1[临时妥协11] / 82001d09 §3.2 | ✅已修复（2026-08-02） | win_wfp.py:756-790 | 已回退为 per-port 方案，Permit 条件含 `_build_port_eq_condition(port)`，仅放行端口范围内端口（60080-60089）。uninstall 保留固定 base_key 清理以兼容升级残留。实测端口收窄后 PPT 生成正常。详见 #1。 |
| 6 | RESTRICTED_TOKEN_FLAGS 临时去掉 WRITE_RESTRICTED(0x8)（token 组，在此确认） | e8fb0a69 §3.2[根因15] | ❌仍存在 | win_constants.py:75 | `RESTRICTED_TOKEN_FLAGS = DISABLE_MAX_PRIVILEGE | SANDBOX_INERT  # 临时去掉 WRITE_RESTRICTED(0x8) 定位 0xC0000142`。`WRITE_RESTRICTED = 0x8` 已定义（line 71）但未 OR 进 flags。注释仍标"临时"。**附加事实**：受限 token 实际未用于起 child（exec 用 `_get_runner_primary_token()`，win_exec.py:1303），故此降级对 exec 路径无实际影响（受限 token 是 dead code）。 |
| 7a | _WFP_ERROR_NAMES 字典 0x80320032 是死条目（SDK FWP_E_FILTER_NOT_FOUND 应为 0x80320003） | e8fb0a69 §3.1[修复9] | ❌仍存在 | win_wfp.py:86 | `0x80320032: "FWP_E_FILTER_NOT_FOUND (delete)"` 仍在字典中。实际 not-found 集合用对了（`_DELETE_NOT_FOUND = {0x80320031, 0x80320003}`，win_wfp.py:861），正确值 `0x80320003: "FWP_E_FILTER_NOT_FOUND"` 也在字典中（win_wfp.py:58）。但 `0x80320032` 这条死条目未删除，SDK 中无对应常量，会误导维护者。 |
| 7b | write_sid_ptr 在 CreateRestrictedToken 失败时未 FreeSid | e8fb0a69 §3.2[修复18] | ❌仍存在 | win_exec.py:723-735, 756-757 | `_create_restricted_token` 中 `write_sid_ptr = ctypes.c_void_p()` 后 `AllocateAndInitializeSid(... ctypes.byref(write_sid_ptr))` 成功则 append 进 entries。`finally` 块（win_exec.py:756-757）仅 `kernel32.CloseHandle(h_token)`，**无 `FreeSid(write_sid_ptr)`**。若 CreateRestrictedToken 失败则 leak。单次 runner 调用影响小，但未修。 |
| 8 | WFP uninstall 范围不匹配——uninstall_wfp_filters 默认用 DEFAULT_PROXY_PORT_RANGE，install 用 policy 实际范围，改配置后卸载残留旧 filter | c2c3f5f0 §六.6 / bb1afca0 §4.6 | ⚠️部分解决 | win_wfp.py:805-835 | `uninstall_wfp_filters(permit_port_start=DEFAULT_PROXY_PORT_RANGE_START, permit_port_end=DEFAULT_PROXY_PORT_RANGE_END)` 仍用默认范围作默认参数。**但**：当前 Permit filter 已改为固定 base_key（全端口放行，无 per-port），uninstall 路径（win_wfp.py:825-835）先遍历删旧 per-port 残留（用默认范围 60080-60089），再删固定 key。对当前安装形态（固定 key）卸载正确；对旧版 per-port 残留仅清理默认范围 60080-60089，若旧版曾用非默认范围安装则残留。即"当前安装形态卸载 OK，历史残留清理不完整"。`uninstall()`（win_setup.py）调 `uninstall_wfp_filters()` 用默认范围，未透传实际范围。 |
| 9 | 网络白名单三层模型（WFP Block + Permit loopback:port_range + win_proxy 域名/IP 过滤）是否仍是该模型且有效？port_range 是否带条件？ | 432a5001 §一 / R7 | ⚠️部分解决 | win_wfp.py:740-791；win_proxy.py:40-197；app.py:326 | 三层模型**仍存在**：Block（ALE_USER_ID + 全出站，win_wfp.py:740-754）+ Permit（ALE_USER_ID + loopback，win_wfp.py:756-791）+ win_proxy EgressFilter（app.py:326 启动）。**但 port_range 条件已被去掉**（见 #1），Permit 放行 loopback 全端口而非 port_range。非 loopback 出网仍被 Block（隔离核心有效），但"沙箱出网唯一出口是 win_proxy"的精确性被削弱（loopback 任意端口可直连）。win_proxy 的 EgressFilter 仍工作（deny 优先 + allow OR 语义，win_proxy.py:104-190）。 |
| 10 | *.npmmirror.com 通配符偏宽 | 432a5001 R4 | ❌仍存在 | windows-policy.yaml:165 | `allowed_domains` 仍含 `"*.npmmirror.com"` 和 `"npmmirror.com"`（line 165-166）。`EgressFilter._domain_matches`（win_proxy.py:81-95）对 `*.npmmirror.com` 匹配任意子域 + 主域。未收紧到 `cdn.npmmirror.com`/`registry.npmmirror.com` 具体子域。 |
| 11 | .exec_failed.log 落盘在 workspace 内可被 child 读 | 432a5001 §4.3 / R5 | ❌仍存在 | win_exec.py:1457 | `_failed_log = os.path.join(str(workspace), ".exec_failed.log")`，仍写在 workspace 下（沙箱 child 对 workspace 有 Read）。未落盘到 box-server 私有目录。 |
| 12 | Chrome 预装路径 C:\Program Files (x86)\Qoom\Chrome 硬编码 | 432a5001 R2 | ❌仍存在 | windows-policy.yaml:108 | `read_acl_preinstall` 仍含 `"C:\\Program Files (x86)\\Qoom\\Chrome"`，路径硬编码到产品名 Qoom。未参数化或改用 `executablePath` 单文件 Read。 |
| 13 | WFP loopback 改为全端口放行（为适配 render server 随机端口），去掉 port 条件，沙箱 A 可向沙箱 B control_port 发无认证 IPC。注释自承"临时验证全放开; 定稿方案待定" | 82001d09 §四.1 / P1 | ✅已修复（2026-08-02，WFP 侧） | win_wfp.py:756-790；process.py（control_port 仍无来源校验） | WFP 侧已回退为 per-port Permit（仅放行 60080-60089），沙箱不再能访问 127.0.0.1 任意端口，跨沙箱 control_port 直连被 Block。但 **exec socket 本身仍无鉴权**（process.py/runner `_handle_exec_request` 无 token 握手），见 P0-6/G 组 #9，需独立补鉴权。 |
| 14 | win_wfp.py 大改（343行），bb1afca0 报告里提到的 WFP 问题在当前是否解决 | bb1afca0（整体） | ✅已解决（bb1afca0 提出的 WFP 结构体/GUID/枚举/字节序类问题） | 见下细分 | bb1afca0 提出的 WFP 类问题在 HEAD 已解决：**结构体布局**（FWPM_FILTER0/SUBLAYER0/SESSION0/DISPLAY_DATA0 内嵌，win_wfp.py:284-397）✅；**ALE_USER_ID 改用 SD**（`_build_ale_user_condition` 用 FWP_SECURITY_DESCRIPTOR_TYPE + FWP_ACTRL_MATCH_FILTER，win_wfp.py:527-611）✅；**loopback 字节序**（`LOOPBACK_IPV4_INT = 0x7F000001` host order，win_constants.py:346）✅；**GUID 合法化**（JBX_SUBLAYER_KEY/FILTER_KEY 均合法 UUID，win_constants.py:330-336）✅；**FWP_DATA_TYPE 枚举对齐**（FWP_SID=13/FWP_BYTE_BLOB_TYPE=12/FWP_V4_ADDR_MASK=0x100 等，win_constants.py:286-309）✅。**遗留**：`_add_filter` 的 `keeps_alive` 形参仍为 dead parameter（win_wfp.py:644，函数体内未引用，bb1afca0 §六.8）；`dacl.SetEntriesInAcl` 返回值仍忽略（win_wfp.py:572，bb1afca0 §六.3）；`uninstall_wfp_filters` 默认范围不匹配（见 #8）。这些是 bb1afca0 的 🟡/🟢 级遗留，非 🔴。 |

## 二、补充核对（报告未明确列出但当前状态值得记录）

| # | 问题 | 当前状态 | 证据 file:line | 说明 |
|---|------|---------|---------------|------|
| B1 | egress.default: allow 适配期放开 | ❌仍存在 | windows-policy.yaml:154 | `windows.network.egress.default: allow`，注释"当前为默认允许 (default: allow): Windows 沙箱尚在适配期...先放开保证功能可用"。win_proxy 的 EgressFilter 在 default=allow 下放行所有未命中 deny 的域名。与 WFP loopback 全放行叠加，出网几乎无限制。 |
| B2 | win_proxy docstring 与 OR 实现矛盾（bb1afca0 §六.1） | ✅已解决 | win_proxy.py:104-117 | `EgressFilter.allow` docstring 已更新为 OR 语义："allow 规则按维度独立判定 (OR), 任一命中即放行"，与实现（win_proxy.py:175-185）一致。bb1afca0 报告的 docstring 与实现矛盾已修正。 |
| B3 | _add_filter keeps_alive dead parameter（bb1afca0 §六.8） | ❌仍存在 | win_wfp.py:644 | `_add_filter(... keeps_alive: list[object] | None = None)` 形参仍存在，函数体内未引用（keep-alive 靠调用方 `keeps` 栈帧）。不影响正确性但误导维护者。 |
| B4 | dacl.SetEntriesInAcl 返回值忽略（bb1afca0 §六.3） | ❌仍存在 | win_wfp.py:572 | `dacl = win32security.ACL(); dacl.SetEntriesInAcl([explicit])` 返回值未取。pywin32 版本差异下 DACL 可能未生效（过度封锁，安全侧无害）。 |
| B5 | drain 线程 stdout 超 MAX_STDOUT_BYTES 截断后 child 可能再次阻塞（82001d09 P5） | ❌仍存在 | win_exec.py（82001d09 报告引用，未定位具体行） | 82001d09 报告 P5 标注此为 🟢 低危，HEAD 未改（截断后 break 停止读，child 继续写满 pipe 阻塞到 TerminateProcess 强杀）。仅 stdout 超 MB 级时发生，可接受。 |
| B6 | WFP session 未设 FWPM_SESSION_FLAG_DYNAMIC（e8fb0a69 §4.1 🟡） | ❌仍存在 | win_wfp.py:453 | `session.flags = const.FWP_SESSION_FLAG_NONE`（=0），非 DYNAMIC。非 DYNAMIC session 下 Add 的 filter 默认持久化（期望行为），但 sublayer/filter 的 flags 字段也未设 PERSISTENT（win_wfp.py:477, 661 的 flags=0）。功能 OK 但未明示意图。 |

## 三、汇总

- **本组共核对 14 条（主清单）+ 6 条（补充）= 20 条**
- **已解决（✅）**：10 条
  - #3 FWPM_FILTER0 union 16B
  - #4a filterCondition 字段名
  - #4b V4 GUID 904F
  - #4c V4/V6 分支按 layer GUID 判断
  - #14 bb1afca0 WFP 结构体/GUID/枚举/字节序类问题（整体）
  - B2 win_proxy docstring OR 语义
  - #1 / #5 / #13 Permit filter per-port 回退（2026-08-02 修复，仅放行 60080-60089，实测 PPT 正常）
  - （#2 win_proxy 未启动 → 以 lifespan 绕过）
- **仍存在（❌）**：6 条
  - #6 RESTRICTED_TOKEN_FLAGS 去 WRITE_RESTRICTED（且受限 token 实际未用于 exec）
  - #7a _WFP_ERROR_NAMES 0x80320032 死条目
  - #7b write_sid_ptr 未 FreeSid
  - #10 *.npmmirror.com 通配符偏宽
  - #11 .exec_failed.log 落盘 workspace
  - #12 Chrome 路径硬编码
  - B1 egress.default: allow（有意决策，待 skill CDN 固化后收紧）
  - B3 keeps_alive dead parameter
  - B4 SetEntriesInAcl 返回值忽略
  - B5 drain 截断后 child 阻塞
  - B6 session/filter 未显式 PERSISTENT
- **部分解决（⚠️）**：2 条
  - #8 uninstall 范围（当前 per-port 安装形态 OK，uninstall 默认范围与 policy 默认一致）
  - #9 三层模型（核心隔离有效，port_range 条件已恢复）
- **以其他方式绕过（🔄）**：1 条
  - #2 win_proxy 未在 _create_windows 启动 → 改在 app.py lifespan 启动

## 四、关键结论

1. **WFP ctypes/SDK 对齐类问题已全部修复**（#3、#4a、#4b、#4c、#14）：FWPM_FILTER0 union 16B、filterCondition 字段名、V4 GUID 904F、V4/V6 分支判断、结构体布局/GUID/枚举/字节序均已在 HEAD 修正。WFP filter 在真实 Windows 上能成功安装。

2. **核心隔离（非 loopback 出网 Block）仍有效**：Block filter（ALE_USER_ID + 全出站）拦截沙箱用户所有公网出站，#9 的三层模型骨架仍在。

3. **Permit filter 全端口放行已修复（#1/#5/#13，2026-08-02）**：已回退 `82001d09` 的全端口放行，恢复 per-port Permit（仅放行 `127.0.0.1:60080-60089`，对齐设计文档）。保留了 `82001d09` 引入的 `is_v4` 显式判断修复。实测端口收窄后 PPT 生成正常，render server 未因端口限制失败。WFP 侧跨沙箱 control_port 直连已被 Block（但 exec socket 本身仍无鉴权，见 P0-6）。

4. **win_proxy 启动问题已绕过（#2 → 🔄）**：未在 `_create_windows` 启动，但在 app.py lifespan（win32 分支）启动进程级单例代理，监听 policy 的 port_range。ECONNREFUSED 问题不再存在，但代理是进程级单例（非 per-sandbox）。

5. **`egress.default: allow` 保留为有意决策**（B1）：skill（pptx-craft 等）内部需联网下载资源，`default:deny` + 空白名单会导致任务中断。待 skill 侧把 CDN 资源打包/镜像固化后再收紧为 `default:deny`。前置条件是 skill 侧，非 box-server 单方面能改。

6. **次要遗留集中在文档/清理类**：_WFP_ERROR_NAMES 死条目、FreeSid leak、keeps_alive dead parameter、SetEntriesInAcl 返回值忽略、*.npmmirror.com 偏宽、.exec_failed.log 落盘位置、Chrome 路径硬编码——均非致命，但生产前应清理。

6. **bb1afca0 的 WFP 结构体重写已在 HEAD 落地**（#14）：FWPM_FILTER0/SUBLAYER0/SESSION0/DISPLAY_DATA0 内嵌、ALE_USER_ID 改用 SD、loopback host order、GUID 合法化、FWP_DATA_TYPE 枚举对齐——均已在 HEAD 代码中确认。
