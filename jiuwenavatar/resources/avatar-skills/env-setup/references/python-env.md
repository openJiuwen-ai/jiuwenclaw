# Python 环境（按需阅读）

在 `repo-root` 含 `doc/<module>/` 的业务项目根内安装依赖、跑测试、Lint、构建、Gate 校验或本地 Python 脚本时，**须**使用项目虚拟环境，**禁止**默认用系统全局 `python` / `pip`。

Leader 派发已给出 `python` 绝对路径或 `venv` 目录时，**以派发材料为准**。

## 优先级

1. **项目已有环境**：在目标根探测 `.venv/` → `venv/` → 文档/`pyproject.toml` 声明路径。
2. **无环境时优先 [uv](https://github.com/astral-sh/uv)**：`uv venv .venv`，再 `uv sync` 或 `uv pip install -r requirements.txt`。
3. **降级**：`python -m venv .venv` 后按项目惯例安装依赖。

## 解析 `$PYTHON`

| 平台 | 典型路径 |
|------|----------|
| Windows | `<repo-root>\.venv\Scripts\python.exe` |
| Unix | `<repo-root>/.venv/bin/python` |

pr-gate 中 `<repo-root>` 即 `--repo` 传入的 **`LOCAL_REPO`** 绝对路径。

可选自检：

```powershell
& <repo-root>\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
```

输出应指向 venv，而非 Store/系统 Python。

## 禁止修改虚拟环境与 site-packages

**严格禁止**为通过测试、规避报错或临时修复而直接改动 Python 运行环境内的第三方包源码：

- **虚拟环境目录**：`.venv/`、`venv/` 及 Leader 派发指定的 `venv` 路径；创建 venv、用 `pip`/`uv` 安装依赖除外。
- **`site-packages`**：已安装第三方包目录内的 `.py`、`.pyi`、`.so`、元数据等。
- **系统/全局 Python 环境**：非项目 venv 的全局 `site-packages` 与解释器自带库目录。

**正确做法**：在项目业务源码或测试代码中修复；通过 `requirements.txt` / `pyproject.toml` 调整依赖版本，并用 `$PYTHON -m pip` / `uv` 重装；必要时向 Leader 说明需升级/替换/fork 依赖。

## 安装与执行

- **安装依赖**：`& $PYTHON -m pip install …` 或 `uv sync` / `uv pip install …`，在 `repo-root` 下。
- **跑 Python 测试**：`& $PYTHON -m pytest …` 等；凡调用 Python 解释器的命令一律用 `$PYTHON`。
- **Lint / 类型检查 / 脚本**：`& $PYTHON -m ruff check`、`& $PYTHON scripts/foo.py` 等。
- **Node.js / 前端命令**：见 [node-env.md](node-env.md)。
- **交付说明**：汇报验证结果时写明 `$PYTHON` 路径及实际执行的命令。

## Leader：Gate 与派工

G0 探测见 `skills/env-setup/SKILL.md`（`env_bootstrap.py`）。G1–G5 的 Gate 脚本须在 `repo-root` 虚拟环境中执行。子 agent 须直接写入 `doc/<module>/` 下正式产物后再运行校验脚本；**禁止**临时文件落盘、stdin 管道传 Markdown、多行 `python -c "…"` 复现校验。

示例 G1：

```powershell
& D:\path\to\repo\.venv\Scripts\python.exe skills/dev-analyzer/scripts/check_requirements.py --module <module> --type Bug --repo-root D:\path\to\repo
```

**创建环境**：

```powershell
uv venv .venv
uv sync
# 或
uv pip install -r requirements.txt
```

**派工材料**宜附带 **`python`**（venv 解释器绝对路径）或 **`venv`**（相对 `repo-root` 的目录）。

## Tester：module 与 pr-gate

- module：执行 Python 测试前确定 `$PYTHON`；`[x]` 须有 venv 内执行证据。
- pr-gate：`pr_unit_test_runner.py` 用 **`$PYTHON`** 调用；`unit_test_plan.json` 的 `command` 使用 LOCAL_REPO venv。
- 环境缺失或依赖装不齐：**不得**勾 `[x]`。

## 换源

**优先**沿用仓库已有 pip/uv index 与 CI。慢或超时时可用清华/阿里云镜像（见原 skill 命令示例）；**禁止**仅为换源改 lockfile。

## skills 仓与业务仓分离

- 目标代码与 `doc/<module>/` 在 **业务 `repo-root`**；venv 在该根创建。
- Gate 脚本在 skills 仓时，`--repo-root` 仍指向业务仓；`skills/...` 相对 **`skills_root`**。
- 见 [aidlc-common/references/skills-paths.md](../../aidlc-common/references/skills-paths.md)。

## 常见问题

| 现象 | 处理 |
|------|------|
| 缺包 | `repo-root` 内 uv/pip 安装后重跑 |
| PyPI 慢 | 项目镜像或换源 |
| 指向系统 Python | 改用 `.venv` 内解释器 |
| 子 agent 解释器不一致 | Leader 重派时写明绝对路径 |
