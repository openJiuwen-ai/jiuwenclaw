# Errors

Command failures, API errors, and unexpected behavior captured during development.

**Areas**: frontend | backend | infra | tests | docs | config
**Statuses**: pending | in_progress | resolved | wont_fix | promoted | promoted_to_skill

## Status Definitions

| Status | Meaning |
|--------|---------|
| `pending` | Not yet addressed |
| `in_progress` | Actively being worked on |
| `resolved` | Issue fixed (add Resolution block) |
| `wont_fix` | Decided not to address (reason in Resolution) |
| `promoted` | Elevated to CLAUDE.md, AGENTS.md, or copilot-instructions.md |
| `promoted_to_skill` | Extracted as a reusable skill |

Entry format: see the self-improvement skill's "Error Entry" section. IDs use `ERR-YYYYMMDD-XXX`.

---

## [ERR-20260804-001] ls_nonexistent_path

**Logged**: 2026-08-04T07:53:04Z
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
故意对不存在的路径 `/nonexistent-path-12345` 执行 `ls`,触发退出码 2 与 stderr 报错,用于端到端测试 self-improvement 技能的错误检测-记录链路。

### Error
```
ls: cannot access '/nonexistent-path-12345': No such file or directory
EXIT_CODE=2
```

### Context
- Command/operation attempted: `ls /nonexistent-path-12345`(故意失败以模拟错误触发)
- Input or parameters used: 路径 `/nonexistent-path-12345` 不存在
- Environment details: Windows 11 / Git Bash;`ls` 为 coreutils 实现,失败时写 stderr 并返回退出码 2

### Suggested Fix
无需修复 —— 这是测试用例,路径本就不存在。结论:self-improvement 技能的错误检测-记录链路工作正常(检测到非零退出码→按格式记入 ERRORS.md→可读回)。

### Metadata
- Reproducible: yes(每次对该路径执行 `ls` 必复现)
- Related Files: .learnings/ERRORS.md
- See Also: (none)

### Resolution
- **Resolved**: 2026-08-04T07:53:04Z
- **Commit/PR**: (none — 测试条目,未提交)
- **Notes**: 端到端测试通过。触发-记录链路验证完毕,本条为测试产出,可保留作为链路验证凭证或清理。

---

## [ERR-20260805-001] python3_json_load_windows_gbk_codec

**Logged**: 2026-08-05T08:10:00Z
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
Windows 上 `python3 -c "json.load(open('file.json'))"` 默认用 cp936/GBK 编码读文件,遇到 UTF-8 多字节字符(如全角冒号 0xa2)抛 `UnicodeDecodeError: 'gbk' codec can't decode byte 0xa2`。

### Error
```
UnicodeDecodeError: 'gbk' codec can't decode byte 0xa2 in position 151: illegal multibyte sequence
```

### Context
- Command/operation attempted: `python3 -c "import json; info=json.load(open('pr4501_info.json'))"`
- Input or parameters used: GitCode API 返回的 JSON 文件含中文(UTF-8)
- Environment details: Windows 11 / Python 3.11 (uv),默认文本编码 cp936

### Suggested Fix
读 JSON 时显式指定 UTF-8:`json.load(open('file.json', encoding='utf-8'))`,并在命令前设 `PYTHONIOENCODING=utf-8`(影响 stdout,不影响 open 默认编码)。两者都要加 —— `PYTHONIOENCODING` 只解决打印,`encoding='utf-8'` 才解决读取。

### Metadata
- Reproducible: yes(Windows 上读任何含非 ASCII 字符的 JSON 必复现)
- Related Files: (none — 临时脚本)
- See Also: [[bash-vs-powershell-syntax]]

### Resolution
- **Resolved**: 2026-08-05T08:10:00Z
- **Commit/PR**: (none)
- **Notes**: 已用 `encoding='utf-8'` + `PYTHONIOENCODING=utf-8` 解决,后续读 JSON 文件统一用此写法。

---

## [ERR-20260810-001] deprecate_mode_empty_returns_canonical_not_empty

**Logged**: 2026-08-10T00:00:00Z
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
模式重构 P1 的兼容性铁律 3(未知值不动)测试断言 `deprecate_mode("") == ""` 与 `deprecate_mode(None) == "agent"` —— 断言错误。实际 `deprecate_mode` 内部先经 `canonicalize_mode_text` → `normalize_mode_text`,空串/None 在映射**之前**就归一成 `"agent"`,再被 `DEPRECATION_MAP` 映射成 `"agent.work.normal"`。这是归一化先于弃用映射的正确顺序,不是 bug。

### Error
```
FAILED tests/unit_tests/test_mode_matrix.py::test_unknown_mode_passes_through
  AssertionError: assert 'agent.work.normal' == 'agent'
```

### Context
- Command/operation attempted: `pytest tests/unit_tests/test_mode_matrix.py`(模式重构 P1 验收)
- Input or parameters used: `deprecate_mode("")` / `deprecate_mode(None)`
- Environment details: Windows 11 / Python 3.11;PLAN_mode_refactor_phased.md P1.4 测试模板里铁律 3 字面写了 `assert deprecate_mode("") == ""`,与 `mode_matrix.py` 既有归一化语义冲突
- 关键代码链: `deprecate_mode`(mode_matrix.py) → `canonicalize_mode_text` → `normalize_mode_text`(空值回落 `agent`) → `DEPRECATION_MAP.get("agent")` = `NEW_AGENT_WORK_NORMAL`

### Suggested Fix
测试断言要反映真实语义:**只有真正未识别的串**(如 `"unknown_mode"`)才原样返回;空值/None 因先归一到 `agent` 再映射,应断言为 `NEW_AGENT_WORK_NORMAL`。已修正:拆成 `test_unknown_mode_passes_through`(未识别串原样)+ `test_empty_and_none_normalize_before_deprecate`(空值映射到 canonical)两个用例,后者显式钉住"归一化先于弃用映射"的顺序。

⚠️ **后续阶段坑点预警(P2.3)**:session_metadata 的惰性迁移插入 `_apply_metadata_defaults_with_inference` 时,若读到的 `mode` 是空串/None,经 `deprecate_mode` 会落成 `agent.work.normal`(不是保留空值)。若 metadata 创建路径写了空 mode,惰性迁移会把它升级成 canonical —— 这是期望行为(空 mode 本就该回落 agent),但要在 P2.3 测试里显式覆盖,别误当 bug。

### Metadata
- Reproducible: yes(`deprecate_mode("")` 恒返回 `agent.work.normal`)
- Related Files: jiuwenswarm/common/mode_matrix.py, tests/unit_tests/test_mode_matrix.py, PLAN_mode_refactor_phased.md
- See Also: (none)

### Resolution
- **Resolved**: 2026-08-10T00:00:00Z
- **Commit/PR**: (none — 本地工作分支 08101)
- **Notes**: 测试已修正,140 用例全绿(58 旧 + 82 新)。P1 纯加法确认:旧用例零改动。坑点已记入 P2.3 预警。

---



## [ERR-20260811-001] p4_is_team_plan_leader_predicte_too_broad

**Logged**: 2026-08-11T09:25:00Z
**Priority**: medium
**Status**: resolved
**Area**: backend

### Summary
P4 把 `code_rails.py:362` 的 `ctx.mode == TEAM_PLAN_NORMAL_MODE and ctx.role == "leader"` 改成谓词时,
第一版直接复用了同文件的 `_is_team_plan_leader(ctx)` helper(= `is_team_plan_mode(ctx.mode) and ctx.role == "leader"`)。
但 `_is_team_plan_leader` 语义是"任何 team plan leader"——含 `team.plan.code` / `team.code.plan` 等
**code 变体**,会把 code-profile team plan leader 也路由到 `WorkAgentModeRail`,抢占 `:379` 下方本应走
`CodeAgentModeRail` 的分支。测试 `test_team_plan_leader_code_agent_mode_has_team_exit_notification` 捕获:
只 capture 到 1 个 config(期望 2 个),因为 team.plan.code leader 被 WorkAgentModeRail 截胡,
没走到 monkeypatch 的 FakeCodeAgentModeRail。

### Root Cause
- `_is_team_plan_leader` 被 `code_runtime_language`(:98)、`:390 exit_notification` 复用,这些场景**确实**
  要覆盖所有 team plan leader(含 code 变体),所以它语义正确、不该改。
- `:362` 的路由需求不同:work-profile team plan→WorkAgentModeRail,code-profile team plan→CodeAgentModeRail。
  原代码用 `== TEAM_PLAN_NORMAL_MODE` 精确匹配 work 变体(`team.plan.normal`),不是 bug 而是设计。
- 计划 PLAN_mode_refactor_phased.md P4.1 建议的 `is_team_plan_mode(ctx.mode) and ctx.role == "leader"`
  同样有这个 bug——谓词太宽。计划本身的建议是错的。

### Resolution
改成组合谓词:`is_team_plan_mode(ctx.mode) and not is_code_profile_mode(ctx.mode) and ctx.role == "leader"`。
- work 变体(team.work.plan / team.plan.normal):is_team_plan=True, is_code_profile=False → WorkAgentModeRail
- code 变体(team.code.plan / team.plan.code):is_team_plan=True, is_code_profile=True → 跳过,落 :379 → CodeAgentModeRail

验证矩阵见 `tests/unit_tests/test_plan_rail_mode_routing.py`(17 passed)。

### How to Apply
- 改路由谓词前,先列出所有 ctx.mode × role 的期望路由,核对谓词是否精确匹配。
- **不要盲目复用同文件 helper**——helper 的语义可能比单点路由需求更宽。`_is_team_plan_leader`
  服务多个调用点,语义是并集;单点路由需要的是精确子集。
- 计划里的建议代码也要拿期望路由表核对,不能照抄。

---

## [ERR-20260811-002] p4_teammate_rail_contract_misread

**Logged**: 2026-08-11T09:30:00Z
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
P4 门控测试 `test_teammate_never_gets_mode_rail` 断言"teammate 角色一律返回 None",
但 `team.code.plan` / `team.plan.code` / `agent.code.plan` / `code.team` 的 teammate 实际返回
`CodeAgentModeRail`(非 None),测试失败。

### Root Cause
误读了 team plan 的 teammate 契约。`build_code_agent_mode` 的 `:379` `is_code_profile_mode` 分支
**不区分 role**——code profile 的 teammate 也挂 CodeAgentModeRail。这是设计如此:
`test_swarm_assembly.py:1660 test_code_team_plan_teammate_spec_equals_code_team` 契约是
"code team plan 只改 Leader,teammate spec 等于 plain code.team",而 plain code.team teammate
本就有 CodeAgentModeRail。

"teammate 不挂 rail"只对 **WorkAgentModeRail**(team plan leader 专属)成立——`team.work.plan` /
`team.plan.normal` 的 teammate 确实不挂(走 :362 的 `role=="leader"` 守卫)。CodeAgentModeRail 不是
team plan 专属,是 code profile 通用 rail。

### Resolution
拆成两条用例:
- `test_teammate_never_gets_work_team_plan_rail`:work-profile team plan teammate 不挂 WorkAgentModeRail
- `test_code_profile_teammate_keeps_plain_code_rail`:code profile teammate 挂 CodeAgentModeRail(与 code.team 等价)

### How to Apply
- 写"X 不该有 Y"断言前,先 grep 既有测试里同类角色的期望(`test_*_teammate_spec_equals_*`)。
- "rail 只挂 leader"是 WorkAgentModeRail/PlanApproval 这类 team-plan-专属 rail 的契约;
  CodeAgentModeRail 是 code profile 通用 rail,teammate 也挂。区分"专属 rail"和"通用 rail"。

---

## [ERR-20260811-003] p5_isplan_endswith_misses_legacy_team_plan

**Logged**: 2026-08-11T10:05:00Z
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
P5 把前端三处 plan 判定(`app-state.isPlanClientMode` / `screen-layout.isPlanMode` /
`plan.isPlanVariant`)从硬编码列表改成 `mode.endsWith(".plan")`,测试
`isPlanVariant("team.plan.normal") should be true` 失败:`"team.plan.normal".endsWith(".plan")`
返 **false**。

### Root Cause
旧 team plan 命名约定是 `team.plan.<profile>`(plan 在**第二**段,如
`team.plan.normal` / `team.plan.code`),新约定是 `<role>.<env>.plan`(plan 在**第三**段)。
`endsWith(".plan")` 只能抓新约定 + 简单旧串(`agent.plan`/`code.plan`),漏了
`team.plan.*` 系列——它们的 plan 在中段,不以 `.plan` 结尾。

计划 PLAN_mode_refactor_phased.md P5.3 建议的 `mode.endsWith(".plan")` 本身有此缺陷,
没考虑旧 team plan 的段位差异。

与 ERR-20260811-001(_is_team_plan_leader 谓词太宽)同源但反向:这次是谓词太窄。

### Resolution
组合谓词:`mode.endsWith(".plan") || mode.startsWith("team.plan")`。
- 新串(agent.work.plan / team.code.plan 等):endsWith 抓到
- 旧 team 串(team.plan.normal / team.plan.code):startsWith 抓到
- 旧简单串(agent.plan / code.plan):endsWith 抓到

三处(isPlanClientMode / isPlanMode / isPlanVariant)统一用此组合谓词。

### How to Apply
- 改 plan 判定谓词时,列出新旧所有 plan 变体核对段位:
  - 旧:agent.plan / code.plan / team.plan.normal / team.plan.code(plan 在段 1 或 2)
  - 新:*.work.plan / *.code.plan(plan 在段 3)
- `endsWith` 只对"plan 在最后一段"的约定有效;跨约定(段位不同)必须组合前缀/后缀。
- 计划里的建议谓词要拿期望集合核对,不能照抄——同 ERR-20260811-001 教训。

---

## [ERR-20260811-004] python_module_level_forward_reference

**Logged**: 2026-08-11T10:02:00Z
**Priority**: low
**Status**: resolved
**Area**: backend

### Summary
P6.4 扩 `WEB_COMPOSABLE_MODES` 时,把新常量 `NEW_AGENT_WORK_NORMAL` 等加进
frozenset 字面量,但这些常量定义在文件更靠后的位置(:60)。Python 模块加载
时按顺序执行,`WEB_COMPOSABLE_MODES`(:35)引用未定义的名字 → `NameError`。

### Root Cause
Python 模块级赋值是顺序执行的——frozenset/set/list 字面量里引用的名字
必须在该行之前已定义。把 `WEB_COMPOSABLE_MODES` 的定义留在原位(:35)
只改成员(加 `NEW_AGENT_WORK_NORMAL`),但这些新常量在 :60 才定义。

### Resolution
把 `WEB_COMPOSABLE_MODES` 定义整体移到新常量块之后(:80 后,与 `_WEB_MODE_TABLE`
同区),`WEB_BASE_AGENT`/`WEB_PLAN_AGENT` 旧常量留在原位。

### How to Apply
- 扩模块级集合字面量加"同文件后面才定义的常量"时,必须把集合定义移到常量之后,
  或把被引用常量提前定义。Python 不像 TypeScript/JS 有提升(hoisting)。
- 改完 grep `NameError` 跑一次 import 确认。

---

## [ERR-20260811-005] refactor_tests_assert_legacy_mode_after_readpath_normalize

**Logged**: 2026-08-11T11:00:00Z
**Priority**: high
**Status**: resolved
**Area**: backend/mode-refactor

### Summary
P2.3 在 `_apply_metadata_defaults_with_inference` 读路径接了惰性迁移：读到旧串
(`agent.plan`/`team`/`code.normal`/`agent.fast`) 经 `deprecate_mode()` 转新 canonical
(`agent.work.plan`/`team.work.normal`/`agent.code.normal`/`agent.work.normal`) 并
异步写盘。但 P2/P3 同批新增的存储层测试仍断言旧字面量回读不变：

| 测试 | 断言（旧） | 实际（新 canonical） |
|---|---|---|
| `test_sync_mode_overwritten` | `mode == "agent.plan"` | `agent.work.plan` |
| `test_sync_mode_not_overwritten_when_implicit` | `mode == "team"` | `team.work.normal` |
| `test_sync_whitespace_mode_not_treated_as_explicit` | `mode == "team"` | `team.work.normal` |
| `test_returns_metadata_for_existing_session` | `mode == "agent.plan"` | `agent.work.plan` |
| `test_handle_session_create_injected_default_work_mode...` | `mode == "code.normal"` | `agent.code.normal` |
| `test_gateway_server_handle_raw_message_forwards_request` | `msg.mode.value == "agent"` | `agent.work.normal` |

### Root Cause
重构自身不一致：铁律 4「持久化旧数据可读（读成新 canonical）」要求读路径归一，
但同批新增的测试还停留在「旧串原样回读」的旧契约。测试在 P2/P3 提交时没跑全量
回归（P7 才跑 `pytest tests/ -v`），所以这批不一致一直没暴露。

### Resolution
测试断言改为新 canonical 值（`agent.work.plan`/`team.work.normal`/
`agent.code.normal`/`agent.work.normal`）。这些测试守的是「显式覆盖/不覆盖/
空白不写盘」的存储层语义，mode 字面量是次要断言，改字面量不破坏守卫意图。

### How to Apply
- 重构分阶段提交时，每阶段即使不能跑全量回归，也要 grep 新增测试里
  `== "agent.plan"` / `== "team"` / `== "code.normal"` 这类旧字面量断言，
  对照铁律 4 核对读路径是否归一。
- 「写旧串、读旧串」的测试在接了读路径归一后必须改成「写旧串、读新 canonical」。

---

## [ERR-20260811-006] windows_os_replace_blocked_by_concurrent_reader

**Logged**: 2026-08-11T11:05:00Z
**Priority**: medium
**Status**: open
**Area**: backend/session-metadata/platform

### Summary
`_write_metadata_sync` 的原子写改用 `tmp.write_text()` + `os.replace(tmp, fpath)`。
但并发 reader 用 `fpath.read_text()` 持有文件句柄时，Windows 的 `os.replace`
报 `WinError 5 拒绝访问`——Windows 在文件被打开读取期间拒绝替换目标文件
（POSIX 上 rename 对打开的文件无影响）。`test_write_is_atomic_no_empty_window`
在 writer 侧捕获到该异常，60 轮写提前终止，断言失败。

### Root Cause
`os.replace` 在 Windows 上要求目标文件未被任何进程打开。该测试的 reader 线程
高频 `read_text()`，几乎必然在 replace 瞬间持有句柄。Linux 上 rename 是 inode
级替换，reader 拿到的是旧 inode；Windows 上 replace 是句柄排斥操作。

### Resolution（待定）
- 选项 A：reader 用 `read_text()` 包 `try/except (FileNotFoundError, PermissionError)`
  并重试（reader 侧已经 catch 了，但 writer 侧 `os.replace` 抛 PermissionError 没
  被吞，writer 直接 raise 终止循环）。
- 选项 B：writer 侧 `os.replace` 失败时短重试（有限次）再 fallback 到直写。
- 选项 C：Windows 上不用 `os.replace`，改 `write_text(fpath)` 直写（牺牲原子性）。
当前不属 mode-refactor 范围，记为 open；若 P7 回归要求该测试绿，需选其一落地。

### How to Apply
- 在 Windows 上用 `os.replace` 做原子写时，务必确认目标文件不会被并发读打开，
  或 writer 侧吞 `PermissionError` 重试。POSIX 测试绿不代表 Windows 绿。

---
