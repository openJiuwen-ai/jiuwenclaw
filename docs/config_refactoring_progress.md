# jiuwenswarm 配置项整改改动进度记录

> 本文件记录每次代码改动的可追溯信息，包括 commit、改动范围、验证结果。

---

## 基线信息

| 项 | 值 |
|---|---|
| 上游仓库 | https://gitcode.com/openJiuwen/jiuwenswarm.git |
| 上游基线 commit | `bb5c6427f960ba41f05f6a403b6df1afea7f614b` |
| 上游基线日期 | 2026-08-31T09:41:35+08:00 |
| 个人仓 | https://gitcode.com/haowenzhong1/jiuwenswarm.git |
| 工作分支 | `feat/config_refactoring` |
| committer / author | zhonghaowen@huawei.com |
| commit 格式 | `fix(config_refactoring): step N <English content>` |

---

## 改动记录

### Commit 1: 阶段一 — 迁移引擎重写 + 时间字面量修复 + 版本管理 + 环境校验

| 项 | 值 |
|---|---|
| commit hash | `4cb40b4b0a199594be73d2446432c1c446c40c5a` |
| commit message | `fix(config_refactoring): step 1 rewrite config migration engine v3, preserve comments and user values` |
| 提交时间 | 2026-08-31T17:05:36+08:00 |
| 改动文件数 | 7（含本文档） |
| 行数变化 | +688 / -97 |

**改动文件清单**

| 文件 | 改动类型 | 行数变化 | 说明 |
|------|----------|----------|------|
| `jiuwenswarm/common/config.py` | 重写 + 新增 | +546/-97 | 合并引擎、结构迁移、版本管理、备份、环境校验 |
| `jiuwenswarm/common/utils.py` | 修改 | +16/-4 | `ensure_config_migrated_from_template` 接入版本迁移 |
| `jiuwenswarm/resources/config.yaml` | 修复 | +2/-2 | `active_hours` 时间字面量加引号 |
| `jiuwenswarm/resources/config.team.distributed.leader.yaml` | 修复 | +2/-2 | 同上 |
| `jiuwenswarm/resources/config.team.distributed.teammate.yaml` | 修复 | +2/-2 | 同上 |
| `deploy/yuanrong/conf/gateway-config-yuanrong.template.yaml` | 修复 | +2/-2 | 同上 |
| `docs/config_refactoring_progress.md` | 新增 | +142 | 本进度记录文档 |

**改动详情**

#### 1. 合并引擎重写（config.py，解决 D1/D2/D3/D4）

| 新增函数 | 作用 | 对应缺陷 |
|----------|------|----------|
| `merge_template_into_user()` | 就地在 user 的 `CommentedMap` 上合并，保留注释/格式；无 depth 守卫；user 独有字段天然保留 | D1 D2 D4 |
| `_merge_list()` | 列表按 key 增量下发（`permissions.rules` 等 5 条路径）；未登记路径 user-wins | D3 |
| `_collect_orphans()` | 采集 user 独有字段，保留 + 告警，不删除 | D4 |
| `_fingerprint_yaml()` | 序列化 YAML 树为字符串指纹，用于幂等判据 | — |
| `MergeReport` | 迁移操作报告 dataclass（added/list_added/migrated/orphaned） | — |

| 修改函数 | 改动 |
|----------|------|
| `_deep_merge()` | 改为向后兼容别名，委托给 `merge_template_into_user` |
| `migrate_config_from_template()` | 重写合并流程：结构迁移 → 采集 orphan → 就地合并 → 指纹幂等 |

**新增模块级变量**
- `_RT_YAML` — 模块级 ruamel 实例（保注释写路径），供 `_fingerprint_yaml` 使用
- `LIST_MERGE_BY_KEY` — 列表增量下发的路径→key 映射表
- `LIST_USER_WINS` — 整体 user 优先的列表路径集合

#### 2. 结构迁移 — 根级 pre-pass（config.py，解决 D4）

| 新增函数 | 作用 |
|----------|------|
| `apply_structural_migrations()` | 根级结构迁移，set-if-absent 语义 |
| `_migrate_legacy_agent_submode_memory()` | 旧 plan/fast 子模式 memory → `modes.agent.memory`（保持原逻辑） |
| `_get_by_path()` / `_has_path()` / `_set_by_path()` / `_del_by_path()` | 点分路径操作原语 |

**迁移映射表 `DEPRECATED_FIELDS`**
- `heartbeat.every` → `health_check.every`
- `heartbeat.target` → `health_check.target`
- `heartbeat.active_hours` → `health_check.active_hours`
- `modes.agent.fast.memory.enabled` → `modes.agent.memory.enabled`
- `modes.agent.plan.memory.enabled` → `modes.agent.memory.enabled`

#### 3. 版本管理 + 备份（config.py，解决 D6）

| 新增函数 | 作用 |
|----------|------|
| `run_versioned_migration()` | 版本驱动迁移；区分 fresh install / upgrade / normal startup |
| `_read_version()` / `_write_version()` | 读写 `version.json` |
| `_vcmp()` | 点分数字版本比较 |
| `backup_config()` | 迁移前快照到 `backups/` 目录 |
| `_prune_backups()` | 保留最近 10 份备份 |

**新增常量**
- `CONFIG_VERSION = "2.0"`
- `MIGRATIONS` — 迁移链（暂空，框架就绪）

**接入点**（utils.py）
- `ensure_config_migrated_from_template()` 在模板合并前先调用 `run_versioned_migration()`

#### 4. 环境变量校验（config.py，解决 D16/D17）

| 新增函数 | 作用 |
|----------|------|
| `validate_env_vars()` | 校验无默认值 `${VAR}` + feature 已启用的变量是否在 `.env` 定义 |
| `check_env_vars_on_startup()` | 启动告警，不阻塞 |
| `_is_feature_enabled()` | feature 门控检查（如 `task_memory.enabled`） |

**门控表 `_FEATURE_REQUIRED_ENV`**
- `task_memory` → `TASK_MEMORY_LLM_MODEL` / `TASK_MEMORY_EMBED_MODEL` / `TASK_MEMORY_API_KEY` / `TASK_MEMORY_API_BASE`

#### 5. 时间字面量加引号（5 个 YAML 文件）

将 `active_hours` 的 `start: 08:00` / `end: 22:00` 改为 `start: "08:00"` / `end: "22:00"`，消除 PyYAML 1.1 的六十进制解析差异。

**修复前**：PyYAML 将 `end: 22:00` 解析为 `1320`(int)，ruamel 解析为 `"22:00"`(str)
**修复后**：两个解析器均解析为 `"22:00"`(str)，0 差异

---

**验证结果（6 项全通过）**

| # | 测试项 | 结果 |
|---|--------|------|
| 1 | 合并引擎：用户值保留 + 独有字段保留 + 规则恢复(21) + 深层补回 + 注释不减 + 幂等 | ✅ |
| 2 | 结构迁移：set-if-absent（两值并存时保留用户新值 600，不被旧值 3600 覆盖） | ✅ |
| 3 | 结构迁移：无新路径时从旧值迁移（3600 → health_check.every） | ✅ |
| 4 | 注释保留：迁移前 500 行注释 → 迁移后 520 行（新增子树带来模板注释） | ✅ |
| 5 | 环境变量：正确识别 4 个 bare TASK_MEMORY 变量（无 `:-` 默认值） | ✅ |
| 6 | 指纹幂等：第一次合并有变化 → 第二次合并无变化（不写盘） | ✅ |

---

### Commit 1.1: 阶段一收尾 — 解析器统一 + 列表策略修正 + 环境校验重写 + 验收测试

| 项 | 值 |
|---|---|
| commit message | `fix(config_refactoring): step 1.1 unify YAML parser, fix list strategy, rewrite env validation, add acceptance tests` |
| 改动文件数 | 4 |
| 行数变化 | +398 / -30 |

**改动文件清单**

| 文件 | 改动类型 | 行数变化 | 说明 |
|------|----------|----------|------|
| `jiuwenswarm/common/config.py` | 修正 | +85/-30 | 解析器统一、列表策略修正、环境校验重写 |
| `jiuwenswarm/common/utils.py` | 修改 | +9/-0 | `check_env_vars_on_startup` 接入启动路径 |
| `tests/unit_tests/test_config.py` | 新增 | +334/-0 | §12.2 验收测试 13 条 |
| `docs/config_refactoring_progress.md` | 更新 | — | 本进度记录 |

**改动详情**

#### 1. 统一 YAML 解析器（解决 D5，§7）

| 改动 | 说明 |
|------|------|
| 新增 `_SAFE_YAML = YAML(typ="safe")` | 模块级 ruamel safe 实例（YAML 1.2 读路径） |
| 新增 `_load_yaml_safe(filepath)` | 读路径统一入口，替代 `yaml.safe_load` |
| `_read_with_retry` 改用 `_load_yaml_safe` | 消除 PyYAML 1.1 / ruamel 1.2 类型分叉 |
| 异常处理扩展 | 捕获 `MarkedYAMLError`（ruamel 的解析错误） |

**验收 #8**：时间字面量 `"22:00"` 在两个解析器下均为 str，0 差异。

#### 2. 列表合并策略修正（对齐 §5.2）

| 改动 | 说明 |
|------|------|
| `LIST_MERGE_BY_KEY` 缩减为 2 条 | 仅 `permissions.rules` / `file_guard.paths` 增量下发 |
| `LIST_USER_WINS` 从空集改为 4 条 | `models.defaults` / `channels.feishu.apps` / `channels.xiaoyi.apps` / `tools` 显式 user-wins |

**修正前**：`LIST_MERGE_BY_KEY` 含 5 条（含凭据/模型配置），`LIST_USER_WINS` 为空——与 v3 §5.2 规格不符。
**修正后**：仅安全语义列表增量下发，凭据/模型配置显式 user-wins。

#### 3. 环境变量校验重写（解决 D16/D17，§8.2）

| 改动 | 说明 |
|------|------|
| 新增 `_ENV_REF` 正则 | 与 `resolve_env_vars` 中的 pattern 同源 `r'\$\{([^:}]+)(?::-([^}]*))?\}'` |
| 新增 `scan_env_refs(text)` | 返回 (bare, defaulted) 二元组，单一正则，无集合减法 |
| `validate_env_vars` 重写 | 用 `scan_env_refs` 替代双正则 + 冗余减法 |
| 进程环境纳入已定义 | `defined = set(os.environ)` 优先，避免对 launcher 注入变量误报 |
| 告警消息对齐 §8.3 | `"以下环境变量被 config.yaml 引用（无默认值）且对应功能已启用，但未定义，将解析为空串: %s"` |

**修正前缺陷**：
- `[:-]` 是字符类，匹配单个 `:` 或 `-`，不是 `:-` 序列
- `no_default` 正则要求 `}` 紧跟变量名，本就匹配不到 `${VAR:-x}`，减法冗余
- 某变量若在别处出现过 `${VAR:-x}`，此处的裸 `${VAR}` 会被错误豁免

#### 4. `check_env_vars_on_startup` 接入启动路径

| 改动 | 说明 |
|------|------|
| `utils.py: ensure_config_migrated_from_template()` | 模板合并后调用 `check_env_vars_on_startup()` |

三个启动入口（`app.py` / `app_agentserver.py` / `app_gateway.py`）均通过 `ensure_config_migrated_from_template` 间接触发，无需各处单独调用。

#### 5. 验收测试（§12.2，13 条）

| # | 测试 | 验收点 | 对应缺陷 |
|---|------|--------|----------|
| 1 | `test_deep_dict_template_field_backfilled` | depth 6 dict 节点下模板新增字段补齐 | D1 |
| 3 | `test_permissions_rules_incremental_dispatch` | 模板新增 permissions.rules 条目下发 | D3 |
| 4 | `test_user_custom_rule_preserved_no_duplicate` | 用户自定义规则保留 + 内置规则不重复 | D3 |
| 5 | `test_orphan_field_preserved_and_reported` | 用户独有字段保留 + 告警 | D4 |
| 6 | `test_structural_migration_set_if_absent` | set-if-absent，新值不被旧值覆盖 | D4 |
| 7 | `test_structural_migration_correct_path` | 迁移写入 health_check.every 正确路径 | D4 |
| 8 | `test_parser_consistency` | 读写解析器类型一致 | D5 |
| 9a | `test_fresh_install_no_migration` | fresh install 不触发迁移 | D6 |
| 9c | `test_idempotent_migration` | 重复迁移幂等不写盘 | D6 |
| 10 | `test_backup_single_snapshot` | 备份产生快照 | D6 |
| 11a | `test_env_var_validation_feature_gated` | feature 启用 + bare + 未定义 → 告警 | D16 D17 |
| 11b | `test_env_var_validation_not_enabled_no_warn` | feature 未启用 → 不告警 | D16 D17 |
| 11c | `test_env_var_validation_process_env_counts` | 进程环境变量计入已定义 | D16 D17 |

（验收 #2 注释逐字节比对由集成测试覆盖，见下方实测结果。）

---

**验证结果**

| # | 测试项 | 结果 |
|---|--------|------|
| 1 | 全量 `test_config.py`（76 条）| ✅ 全通过 |
| 2 | 真实模板合并集成测试（注释 590→636、规则恢复 21、幂等） | ✅ |
| 3 | 双解析器差异扫描（4 个 YAML 文件） | ✅ 0 差异 |
| 4 | 预存失败测试 `test_build_config.py::test_desktop_release_wrappers...` | ⚠️ 与本次改动无关（PyInstaller `claude_agent_sdk` 预存问题） |

---

## 待办（阶段二及后续）

| 阶段 | 内容 | 状态 |
|------|------|------|
| P1 | `config.local.yaml` 分层：读 + 写链路 | 未开始 |
| P1 | `services` 段（收窄，不含端口，host 无默认值） | 未开始 |
| P1 | `resolve_agent_server_url` 优先级 env > config | 未开始 |
| P1 | 元戎 overlay + 主模板补 `sandbox`/`team` | 未开始 |
| P1 | 项目级配置（只读不写） | 未开始 |
| P2 | `shell_environment` | 未开始 |
| P2 | `_include` 模块化拆分 | 观察项 |
| P3 | config.py schema 重构 | 观察项 |
