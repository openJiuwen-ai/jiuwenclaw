# 命令超时（Leader 必守）

**双层、禁止无限挂起**：外层工具等待 **≥** 命令/脚本自身 `--timeout`（若存在）。

**Precedence**：各 `skills/*/SKILL.md` 与脚本 `--help` / 已文档化 `--timeout` **优先**于下表。子 agent 遵守 `.claude/agents/dev-*.md` 中的超时规则。

| 场景 | 外层上限 |
|------|----------|
| Gate `check_*.py`、`reviewer_plan_check.py status`、`coder_plan_check.py` / `tester_plan_check.py` 的 `status\|verify`、`scripts/* --help` | **60s** |
| `gitcode-repo` API 脚本、`uv sync`、依赖安装 | **300s** |
| build / test / lint（Leader 亲自执行时） | **120–300s**（更重须告知用户） |
| `pr_unit_test_runner.py` | `--timeout` 默认 **120s**；外层 **≥** 该值 |
| 子 agent 长跑测试/构建 | 由子 agent 按其 skill 执行；Leader **不代跑** |

**命令层**（bash/cmd/PowerShell）：`timeout <秒> <cmd>`，或工具/框架 `--timeout`。禁止裸长跑。

**pr-gate**（派给 tester）：`unit_test_plan.json` 的 `command` 亦须自带超时；runner `--timeout` 仅兜底。

- 超时后记录输出与退出码；可重试或重派子 agent；**禁止**谎称 Gate 已通过；子 agent 超时不得勾 plan checklist
- 整段流水线墙钟预算（如 `total_wall_clock_budget`）与单次 shell 上限相互独立
