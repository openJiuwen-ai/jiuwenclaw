# Code Review: 2d19941c fix:解决沙箱内网络访问问题

- commit: `2d19941c190c5e44a77bf8c24f1493e51cd56898`
- 作者: lby / 2026-07-30
- 文件: `jiuwenbox/src/jiuwenbox/supervisor/win_exec.py` (+29)
- 审查人: 资深 Windows 系统/网络工程代码审查员
- 审查日期: 2026-08-01

## 概述

本 commit 体积小 (+29 行, 单文件), 实际只做一件事: 在 `_create_process_as_user`
（沙箱受限 token 起 child 的最末一跳, `win_exec.py:838`）内, 新增一个**通用 env
注入约定键** `JIUWENBOX_INJECT_ENV` 的解析逻辑: 调用方（agent-core）把子进程
所需的额外环境变量以 JSON 编码塞进该键, 沙箱在此解析后 `setdefault` 注入 child
env, 再 `pop` 删掉该键本身（不泄漏给子进程）。

**注意**: 标题"解决沙箱内网络访问问题"略有歧义。这 +29 行**本身并不设置任何代理
变量**（`HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY` 在 `win_exec.py:923-930` 已由
前一 commit 432a5001 的同函数体注入完毕）。本次修复的"网络访问问题"指的是: 某些
工具（典型如 npm/playwright）需要**工具私有 env 键**（如 `npm_config_proxy`、
`PLAYWRIGHT_BROWSERS_PATH` 等）才能正确走代理/装到隔离目录, 而沙箱侧不该硬编码这些
工具语义——故用"约定键 JSON 注入"把语义责任推回调用方（agent-core）。commit 信息
"需要配合 agent-core 的修改"即指: agent-core 侧需负责解析 `.npmrc`/工具配置并把
映射后的 `npm_config_*` 等键塞进 `JIUWENBOX_INJECT_ENV`。

## 变更范围

仅 `win_exec.py:_create_process_as_user` 函数体, 紧跟代理 env 注入块（`:921-930`）
之后, 在最终 `_push_log` 汇总（`:960`）之前, 插入 29 行约定键解析逻辑
（`win_exec.py:931-959`）。

## 改动分析

### `win_exec.py:938` 🟢 约定键弹出 (防泄漏)
```python
_inject_raw = env.pop("JIUWENBOX_INJECT_ENV", None)
```
用 `pop` 而非 `get`——约定键解析后即从 env 移除, 不进入 child env block（`:969`
`parts = [f"{k}={v}" for k, v in env.items()]`）, 避免子进程看到一个内部约定键。正确。

### `win_exec.py:940-944` 🟡 JSON 解析 + 类型校验
```python
import json as _json
_injected = _json.loads(_inject_raw)
if not isinstance(_injected, dict):
    raise ValueError(f"expected JSON object, got {type(_injected).__name__}")
```
- `import json` 放函数内（局部导入）——风格上略奇怪, stdlib 顶层即可; 但不致错。
- 只接受 JSON object（dict）, 拒数组/标量。合理。
- 🟡 **大小问题**: 无长度上限。若调用方误传超大 JSON, `_json.loads` 会消费大量内存
  且 `_inject_raw` 全量保留在 `env` dict 副本里直到函数结束。沙箱是受限环境, 但 child
  env block 本身也有 Windows 大小限制（~32K WCHAR, 见 `:970` block 构造）, 超大注入会
  在 `create_unicode_buffer(block)` 时失败。建议显式截断或校验 `len(_inject_raw)`。

### `win_exec.py:945-950` 🟢 容错: 解析失败只 warning 不阻断
```python
except (ValueError, TypeError) as _e:
    _injected = None
    _push_log("WARNING", f"JIUWENBOX_INJECT_ENV 解析失败, 已忽略: {_e} raw_len={len(_inject_raw)}")
```
约定键非法不应让 child 起不来——降级为"不注入"是安全的。`raw_len` 入日志便于排障。
捕获 `ValueError`（JSONDecodeError 父类）+ `TypeError`（_inject_raw 非 str 时
loads 抛）覆盖完整。🟢 设计合理。

### `win_exec.py:951-959` 🟡 setdefault 注入 (低优先级冲突)
```python
if _injected:
    _injected_keys = []
    for _k, _v in _injected.items():
        env.setdefault(str(_k), str(_v))
        _injected_keys.append(str(_k))
```
- `str(_k)/str(_v)` 强转——把 int/bool 等 JSON 值统一转字符串（env block 只接受
  字符串）。正确。
- 🟡 **顺序语义**: `setdefault` 意味着注入键**不能覆盖**已有 env（含前面 923-930 已注入
  的 `HTTP_PROXY` 等）。这是好事（防 agent-core 篡改沙箱强制代理指向）, 但也意味着若
  agent-core 想覆盖系统级代理指向, 它做不到——只能补"沙箱没设过的键"（如
  `npm_config_proxy`、`PLAYWRIGHT_BROWSERS_PATH`）。这与注释"工具语义由调用方负责"一致,
  但需文档化该不可覆盖约束, 否则调用方会困惑。
- 🟡 **键名无白名单**: 约定键可注入任意 env（如 `PATH`、`SystemRoot`、`TEMP`）。由于
  `setdefault` 不覆盖, 已有的 `PATH/SystemRoot/TEMP` 等（873-920 行已补）不会被冲掉,
  但**未被补过的敏感键**（如 `LD_PRELOAD`/`PYTHONPATH`/`NODE_OPTIONS`）可被注入。
  这些键在 child 进程内有代码执行/路径劫持能力。考虑调用方即 agent-core 是可信方,
  但若该键经 header 传输且 header 可被构造, 则存在通过 env 注入劫持 child 的路径。
  建议: 维护一个**禁止注入键黑名单**（`LD_PRELOAD`/`PYTHONPATH`/`NODE_OPTIONS`/
  `DYLD_*`/`PATH` 等）, 至少对代码注入类键拒绝。

### `win_exec.py:956-958` 🟢 注入审计日志
```python
_push_log("INFO", f"injected env from JIUWENBOX_INJECT_ENV: keys={_injected_keys}")
```
记录注入的键名列表（不含值）——审计可追溯, 又不泄露敏感值（如 token）。平衡得当。🟢

## 关键代码检视

**代理指向**: 本 commit **未触碰**代理 URL。`_proxy_port_start`（`win_exec.py:78`、
`:1052`）来自 `const.DEFAULT_PROXY_PORT_RANGE_START`, 经 `win_exec.py:923` 构造成
`http://127.0.0.1:{port}` 注入 `HTTP_PROXY` 等。代理是**本机 loopback**（127.0.0.1）,
`NO_PROXY`（`:930`）也放行 loopback——代理 → 代理 不再走代理, 逻辑自洽。**无 SSRF
/中间人风险**: 代理指向可信本机, 出网由 WFP 兜底拦截（`:922` 注释）。🟢

**与前 commit 432a5001 的关系**: 432a5001（`fix:修沙箱内联网，以及其他权限问题`）
在同函数体内**首次**注入了 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY`（即 921-930
那一坨, 见 432a5001 diff 的 +198 行块）+ profile 变量补全 + TEMP 隔离。本 commit 是
432a5001 的**补充修复**: 432a5001 解决"通用代理 env 注入", 本 commit 解决"工具私有
env 键（npm_config_*等）无法由沙箱预知"的尾巴——把语义外移给 agent-core。**两 commit
分工清晰, 本 commit 是补丁而非重做**。🟢

**跨仓耦合（"需要配合 agent-core 的修改"）**: 该约定键 `JIUWENBOX_INJECT_ENV` 是与
agent-core 的**隐性协议**。本仓库侧只定义"接收 JSON dict → setdefault 注入", 协议的
另一半（agent-core 解析 `.npmrc`/工具配置 → 拼 JSON → 塞进 header env）不在本仓库。
风险点:
1. **协议无版本号**: 若 agent-core 侧 JSON schema 变更（如改成 `{keys:[...], values:[...]}`
   数组形式）, 沙箱侧 `isinstance(_injected, dict)` 校验会让它降级 warning, 静默不注入——
   表现为"网络又挂了"且难定位。建议: 约定键内带 `__v` 字段做 schema 版本协商, 或在
   共享文档（非代码）固化 schema。
2. **协议无单测**: 本仓库未见针对 `JIUWENBOX_INJECT_ENV` 的测试（搜索未见 test 引用）。
   约定键解析是纯函数行为, 应加单测覆盖: 正常 dict / 非法 JSON / 非 dict / 空值 /
   超长 / 含危险键 等用例。

## 优点

1. **关注点分离到位**: 沙箱只提供"注入一坨 env"的通用机制, 不认识任何工具语义
   （`.npmrc`→`npm_config_*` 的映射全在 agent-core）。新工具（如 `pip_config_*`）
   只改调用方, 沙箱零改动——扩展性好, 设计意图明确且注释充分。🟢
2. **防泄漏**: `pop` 删约定键, 子进程看不到内部协议键。🟢
3. **容错降级**: 解析失败只 warning 不阻断 child 启动, 符合"约定键非法不应让子进程
   起不来"的容错原则。🟢
4. **审计日志**: 记录注入键名列表, 不记值, 可追溯且不泄密。🟢
5. **不可覆盖性**: `setdefault` 保证 agent-core 不能覆盖沙箱强制代理指向
   （`HTTP_PROXY` 等）, 安全边界保留。🟢

## 问题与风险

| 级别 | 问题 | 位置 |
|------|------|------|
| 🟡 中 | **注入键无白/黑名单**: 可注入 `LD_PRELOAD`/`PYTHONPATH`/`NODE_OPTIONS`/`PATH` 等代码注入类键, agent-core 可信但 header 传输链路若可构造则存在 child 劫持路径。 | `win_exec.py:953-954` |
| 🟡 中 | **协议无版本协商**: 与 agent-core 的 JSON schema 是隐性约定, schema 变更会静默降级（warning + 不注入）, 表现为网络故障且难定位。 | `win_exec.py:938-959` |
| 🟡 低-中 | **无大小上限**: `_inject_raw` 无长度校验, 超大 JSON 在 `create_unicode_buffer(block)` 时才失败（Windows env block ~32K WCHAR 限制）, 失败点离根因远。 | `win_exec.py:938,970-971` |
| 🟡 低 | **无单测**: 约定键解析纯函数行为无测试覆盖。 | — |
| 🟢 低 | **import json 局部化**: 函数内 `import json as _json` 风格略怪, 应顶层。 | `win_exec.py:941` |
| 🟢 低 | **`_injected` 空字典不记日志**: 若 JSON 是 `{}`, `if _injected:` 为假, 静默不注入也不 warning——调用方传空 dict 是否预期行为不明确。 | `win_exec.py:951` |

## 改进建议

1. **加注入键黑名单**（最高优先）:
   ```python
   _FORBIDDEN_INJECT_KEYS = frozenset({
       "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "NODE_OPTIONS",
       "NODE_PATH", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
       "PATH", "SystemRoot", "windir",  # 沙箱已强制管控
       "JIUWENBOX_INJECT_ENV",  # 防递归
   })
   # 循环内:
   if str(_k) in _FORBIDDEN_INJECT_KEYS:
       _push_log("WARNING", f"JIUWENBOX_INJECT_ENV 拒绝注入禁用键: {_k}")
       continue
   ```
2. **加大小上限**: `if len(_inject_raw) > 8192: warning + 跳过`, 避免超大 JSON 触发
   env block 构造失败。
3. **协议版本化**: 约定键内带 `__v: 1`, 校验版本不符时显式 warning（"协议版本不匹配,
   expected v1"）而非静默降级。
4. **加单测**: 对 `_create_process_as_user` 的 env 注入段抽成可测函数
   （如 `_apply_inject_env(env: dict) -> dict`）, 覆盖正常 dict / 非法 JSON / 非 dict /
   空值 / 含禁用键 / 超长 等用例。
5. **空字典行为明确化**: `if _injected:` 改为显式 `if isinstance(_injected, dict) and
   _injected:` 并在空 dict 时 info 日志（"约定键为空 dict, 无注入"）, 区分"没传约定键"
   与"传了空 dict"。
6. **`import json` 提至模块顶层**。

## 小结

本 commit 是 432a5001（沙箱联网修复）的**精准补充**: 不重做代理 env 注入, 而是用一个
"通用约定键 JSON 注入"机制, 把工具私有 env 键（npm_config_* 等）的语义责任外移给
agent-core, 让沙箱保持工具无关。设计意图清晰、注释充分、容错与审计到位, 防泄漏
（pop）与防覆盖（setdefault）处理正确, 代理指向可信本机无 SSRF 风险。

主要可改进点集中在**注入键无黑名单**（中风险, 代码注入类键可被塞入）和**协议无版本
协商**（中风险, 静默降级难定位）。两者均不阻塞合并, 但建议作为后续小 commit 补齐, 并
补单测固化约定键解析行为。整体代码质量良好, 可合并。
