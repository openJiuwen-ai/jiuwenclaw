---
name: skill-standardizer
description: Use for directImport, compliance check, validate, standardize, or reformat of user-uploaded skill packages. Runs standard validation and risk-control scan; packages to output/ when both pass. On failure, report violations and ask_user_question whether to auto-fix; after upload, risk-control re-check auto-retries up to 2 times.
---

# Skill Standardizer

对用户上传到 `<workspace>/skill/` 的 skill 包进行**规范校验**与**风控校验**；两项均通过则打包。不通过则向用户说明问题并征询是否自动修复；修复上传后风控复检不通过时直接重试修复，最多 2 次，仍失败则告知用户原因并停止。

## 上下文（query 中提供）

- **skill-name**：`SKILL.md` frontmatter 的 `name`
- **url**：用户导入 skill 包的访问地址，用于首次风控校验

## 规范校验（必须通过）

运行：`python3 -m scripts.validate <workspace>`

须满足：

- **name**（kebab-case）：仅小写字母、数字、连字符（`^[a-z0-9-]+$`）；不以 `-` 开头/结尾；不含连续 `--`；长度 ≤64；与 `SKILL.md` 所在父目录名一致
- **description**：非空字符串；不得含 `<` 或 `>`；含 CJK 时 ≤512 字符，否则 ≤1024 字符
- **正文**（frontmatter 之后）：非空；行数 ≤500

### 静态规则扫描（必须通过）

- **危险命令**（`scripts/` 内逐行）：如 `rm -rf /`、`chmod 777`、`curl ... | bash`、`eval(`
- **硬编码凭据**（`SKILL.md` 与 `scripts/`）：如 `api_key = "..."`、`sk-...` 等模式
- **路径越界**：skill 包内相对路径的 path 组件不得含 `..`

脚本 stdout 为 `Validation passed.`（通过），或以 `Validation failed:` 开头（未通过，其后一行起为具体原因）；失败时把完整输出原样给用户。

## 风控校验（必须通过）

运行：`python3 -m scripts.safety_scan <skill-name> <url>`

- **首次**（规范+风控同时检查时）：`<url>` 使用 query 中提供的导入包 **url**
- **修复后复检**（已上传打包产物）：`<url>` 使用 `upload_skill.py` 返回的 URL

脚本 stdout 以 `Safety scan passed.` 或 `Safety scan failed:` 开头；失败时把完整输出原样给用户。

## `ask_user_question` 使用限制

**仅在 A.5（初次校验不通过）允许使用 `ask_user_question`**；除此之外整个流程**不得**使用 `ask_user_question`。

## 工作流

### A. 初次校验

1. 在 `<workspace>/skill/` 下定位 skill 根目录。
2. 运行规范校验：`python3 -m scripts.validate <workspace>`
3. 运行风控校验：`python3 -m scripts.safety_scan <skill-name> <url>`
4. **两项均通过**：运行打包 `python3 -m scripts.package <workspace>`，流程结束。
5. **任一项不通过**：
   - 把**不通过的内容**（脚本完整 stdout/stderr）原样输出给用户
   - 使用 `ask_user_question` 询问用户是否需要修改
   - 用户选择**不需要修改**：停止，不打包
   - 用户选择**需要修改**：进入「B. 修复后流程」

### B. 修复后流程（用户同意修改后）

将修复计数 `attempt` 初始化为 0。

1. 根据规范校验与风控校验的失败输出，修改 `<workspace>/skill/` 内原始 skill（遵循下方修改原则）。`attempt += 1`。
2. 运行规范校验：`python3 -m scripts.validate <workspace>`
   - 未通过 → 继续修改直到通过（无需询问用户，仍计入当前 attempt）。
3. 打包：`python3 -m scripts.package <workspace>`。
4. 上传打包产物：`python3 -m scripts.upload_skill <packaged_path>`，记录返回的 URL。
5. 对第 4 步返回的 URL 再次进行风控校验：`python3 -m scripts.safety_scan <skill-name> <uploaded_url>`
   - **通过**：向用户返回成功信息（含上传 URL），流程结束。
   - **不通过**（无需询问用户，直接重试修复）：
     - 若 `attempt < 2`：回到 B.1 继续修复。
     - 若 `attempt >= 2`：向用户输出最终失败原因（含本次风控的完整输出），**停止，不再重试**。

## 修改原则

- **最小改动**：只做为满足规范所必需的改动。
- **优先保持语义**：不改变原 skill 核心用途与行为。
- **目录名与 name 对齐**：使 `name` 与目录名严格一致（以最小破坏为准）。
- **description**：超长则压缩；含中文时控制在 512 字符内，否则 1024 字符内；不得含尖括号。
- **name**：须改为合法 kebab-case，并与目录名一致。
- **正文**：先删冗余，仍超过 500 行则拆到 `references/` 并用相对路径引用（仅统计 `SKILL.md` 正文行数）。
- 正文须保留必要使用说明，不得清空正文仅靠空目录通过校验。

## Hard rules

1. 不要引入与本 Skill 无关的其他流程。
2. 只在 `<workspace>/skill/` 与 `<workspace>/output/` 范围内读写。
3. **规范校验未通过不得打包**；**修复后未通过上传 URL 的风控校验不得向用户宣告可以上架**。
4. 上传后风控复检不通过时**直接重试修复**（不询问用户），**最多 2 次**；超过后必须停止并告知用户失败原因。
