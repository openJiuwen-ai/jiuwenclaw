---
name: env-setup
description: 业务仓环境统一入口：G0 用 env_bootstrap.py 探测并写任务卡；Python/Node 安装、Gate 执行、换源、禁止改 site-packages/node_modules 见 references 按需阅读。触发：G0 环境、python/node_root、venv、package.json、环境自检。
metadata:
  short-description: Unified repo env skill; G0 probe script; python/node details in references/.
  category: orchestration
  load_policy: conditional
  depends_on: []
  gates:
    - G0
---

# env-setup（环境与工具链）

**唯一环境 skill**。Python/Node 细则在本目录 **`references/`**（无独立 `python-env` / `node-env` skill 目录）。

| 角色 | 加载方式 |
|------|----------|
| **Leader** | G0：本文件 + `env_bootstrap.py`；修复环境时按需读 references |
| **子 agent** | 依赖 `env-setup`；**以任务卡 `python`/`node_root`/`pm` 为准**；细则按需读 references |

## References（按需）

| 文件 | 何时读 |
|------|--------|
| [references/python-env.md](references/python-env.md) | 装依赖、跑 pytest/Lint、Gate 脚本、`$PYTHON`、禁止改 site-packages |
| [references/node-env.md](references/node-env.md) | 存在 `package.json`、装前端依赖、跑 `npm run`、禁止改 node_modules |

## G0 流程（Leader）

```powershell
& $PYTHON skills/env-setup/scripts/env_bootstrap.py --repo-root <repo-root>
```

1. `ok: false` → 按 `hints` 读对应 reference 在 **业务 `repo-root`** 修复 → 重跑直至 `ok: true`。
2. 将 `task_card_env` 写入任务卡；每次 spawn **原样附带**。
3. 禁止用系统全局 `python`/`npm` 跑 Gate 或子 agent 验证。

`--format task-card` 仅导出任务卡字段。脚本用的解释器可与业务仓 venv **不是同一个**（见上文 `$PYTHON` 说明）。

## 任务卡环境块

```markdown
## 环境（G0）

- **python**：`<repo-root>/.venv/Scripts/python.exe`
- **node_root**：`<repo-root>`（无前端省略）
- **pm**：`pnpm`（无前端省略）
- **env_verified**：env_bootstrap.py exit 0
```

## 探测信号

| 信号 | 条件 |
|------|------|
| Python | 默认需要（Aidlc Gate / `doc/` 等） |
| Node | `repo-root/package.json` |

## 禁止

- skills 仓 venv 验证业务仓（见 [references/python-env.md](references/python-env.md) §分离）。
- `ok: false` 仍 spawn 或 G0 PASS。
- 每 Gate 重复全量 `uv sync` / `npm ci`（除非依赖变更）。

## 脚本

| 脚本 | 作用 |
|------|------|
| `scripts/env_bootstrap.py` | 只读探测；输出 JSON / task-card |
