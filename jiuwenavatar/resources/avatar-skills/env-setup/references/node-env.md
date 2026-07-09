# Node.js 环境（按需阅读）

仓库含前端或 Node 工具链时，安装依赖、构建、Lint 与跑 JS/TS 测试**须**遵循本节，**禁止**默认用未对齐版本的系统全局 `node`/`npm`。

Leader 派发已给出 `node_root`、`node_version` 或前端工作目录时，**以派发材料为准**。

## 适用范围

- 存在 `package.json` 的 `repo-root` 或 monorepo 子包。
- 无 `package.json` 时本节不适用；Python 见 [python-env.md](python-env.md)。

## 优先级

1. **对齐 Node 版本**：`.nvmrc` → `.node-version` → `package.json` 的 `engines.node` → README/CI。
2. **解析包管理器**：`pnpm-lock.yaml` → pnpm；`yarn.lock` → yarn；`package-lock.json` 或仅 `package.json` → npm。
3. **安装依赖**：有 lockfile 时优先 `npm ci` / `pnpm install --frozen-lockfile` / `yarn install --frozen-lockfile`。
4. **执行命令**：优先 `package.json` 的 `scripts`。

## 解析工作目录与命令

| 变量 | 含义 |
|------|------|
| `$NODE_ROOT` | 含目标 `package.json` 的目录；默认 `repo-root` |
| `$PM` | `npm` / `pnpm` / `yarn` |

```powershell
node -v
npm ci
npm run test
```

## 禁止修改 node_modules

**严格禁止**直接改 `node_modules/` 内第三方源码。应在业务源码或 `package.json` / overrides 中修复并重新安装。

## 安装与执行

- 在 `$NODE_ROOT` 用 `$PM` 安装；有 lockfile 时优先 frozen/ci。
- **与 Python 并存**：Python 用任务卡 `python` / [python-env.md](python-env.md)；环境独立。
- 汇报时写明 `node -v`、`$PM`、`$NODE_ROOT` 与实际 scripts。

## Leader：Gate 与派工

G0 探测见 `skills/env-setup/SKILL.md`。派工材料宜附带 **`node_root`**、**`pm`**、（可选）**`node_version`**。

## Tester：module 与 pr-gate

- module：跑 Node 测试前确定 `$NODE_ROOT`、`$PM`、Node 版本。
- pr-gate：`unit_test_plan.json` 的 `command` 在 LOCAL_REPO 的 `$NODE_ROOT` 下可运行；`pr_unit_test_runner.py` 本身仍用业务仓 **`$PYTHON`** 调用。

## 换源

**优先**仓库 `.npmrc` / pnpm / yarn 配置。慢时可用 npmmirror；**禁止**仅为换源改 lockfile。

## skills 仓与业务仓分离

- `node_modules` 以**业务仓**目录为准；勿在 skills 仓 `node_modules` 验证业务源码。

## 常见问题

| 现象 | 处理 |
|------|------|
| Node 版本与 CI 不一致 | 切换声明版本或登记限制 |
| 缺依赖 | `$NODE_ROOT` 内 `$PM` 安装后重跑 |
| monorepo 多 package.json | 按改动归属选 `$NODE_ROOT` |
| runner 与 lockfile 不一致 | 以 lockfile/README 更新 `command` |
