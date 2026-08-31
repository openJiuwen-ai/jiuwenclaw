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
