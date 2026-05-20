import type { CommandContext } from "../types.js";

export type ScopeKey = "project" | "personal" | "both";

export interface ExistingFiles {
  jiuwenclawMd: boolean;
  jiuwenclawLocalMd: boolean;
  claudeMd: boolean;
  claudeLocalMd: boolean;
  agentsMd: boolean;
  openjiuwenMd: boolean;
  cursorRules: boolean;
  copilotInstructions: boolean;
}

export interface BuildInitPromptArgs {
  rootDir: string;
  scopeKey: ScopeKey;
  language: "zh" | "en";
  existing: ExistingFiles;
}

// ---------------------------------------------------------------------------
// Language resolution
// ---------------------------------------------------------------------------

export function resolveLanguage(_ctx: CommandContext): "zh" | "en" {
  // 当前方案：best-effort from LANG env; 后续可读 config.
  const lang =
    typeof process !== "undefined" ? (process.env.LANG ?? "") : "";
  return /^zh/i.test(lang) || /CN$/i.test(lang) ? "zh" : "en";
}

// ---------------------------------------------------------------------------
// Prompt builder
// ---------------------------------------------------------------------------

export function buildInitPrompt(args: BuildInitPromptArgs): string {
  return args.language === "zh" ? buildInitPromptZh(args) : buildInitPromptEn(args);
}

// ---------------------------------------------------------------------------
// English (authoritative source)
// ---------------------------------------------------------------------------

function buildInitPromptEn({ rootDir, scopeKey, existing }: BuildInitPromptArgs): string {
  const scopeLine = SCOPE_DESCRIPTION_EN[scopeKey];
  return `Set up a minimal JIUWENCLAW.md (team-shared) and optionally JIUWENCLAW.local.md (personal) for this repository.

These files are auto-loaded into every coding-mode session by ProjectMemoryRail, so they must be CONCISE — only include what the assistant would get wrong without them.

## CRITICAL Constraints (read first, do not violate)

1. **All file operations MUST use absolute paths rooted at: \`${rootDir}\`**
   Never use relative paths. When writing or editing, always construct \`${rootDir}/<filename>\`.
2. **Do NOT use the \`coding_memory_read\` / \`coding_memory_write\` / \`coding_memory_edit\` tools in this command.** Those are for session-level auto-memory, a different system. /init produces static project documents via the file write tools only.
3. **Existing files pre-detected in workspace root**:
  - JIUWENCLAW.md: ${yesNo(existing.jiuwenclawMd)} ${existing.jiuwenclawMd ? "— you MUST read it first, propose a diff, then use `ask_user` with `questions` to ask the user whether to apply. Example: `ask_user(query='Update JIUWENCLAW.md?', questions=[{question: 'JIUWENCLAW.md already exists. What would you like to do?', header: 'Update', options: [{label: 'Apply update', description: 'Merge the proposed changes into the existing file'}, {label: 'Skip (keep current)', description: 'Leave the file unchanged and continue'}], multi_select: false}])`. If user chooses 'Apply update', use Edit to apply the diff; if 'Skip', leave the file unchanged and continue. NEVER silently overwrite." : ""}
   - JIUWENCLAW.local.md: ${yesNo(existing.jiuwenclawLocalMd)} ${existing.jiuwenclawLocalMd ? "— propose additions via Edit only, never overwrite." : ""}
   - Legacy reference files (do NOT delete or rewrite; you may link to them): CLAUDE.md=${yesNo(existing.claudeMd)}, CLAUDE.local.md=${yesNo(existing.claudeLocalMd)}, AGENTS.md=${yesNo(existing.agentsMd)}, OPENJIUWEN.md=${yesNo(existing.openjiuwenMd)}, .cursorrules=${yesNo(existing.cursorRules)}, .github/copilot-instructions.md=${yesNo(existing.copilotInstructions)}
4. **When the explore sub-agent runs bash commands**, always prefix with \`cd ${rootDir} && ...\` or use \`git -C ${rootDir}\` — sub-agent CWD is not guaranteed to equal \`${rootDir}\`.
5. **Always prefer \`task_tool\` with \`subagent_type: "explore_agent"\` when it is available.** If \`task_tool\` is unavailable for this turn, silently FALL BACK to \`glob\` / \`grep\` / \`read_file\` / \`bash\` yourself.
6. **Default to a single \`task_tool\` / \`explore_agent\` call.** If the repository is clearly large, a monorepo, or one pass does not gather enough signal, you may split the work across multiple explore sub-agents; only parallelize when there is a clear benefit, to avoid duplicate scanning and noisy result merging.

## Step 1: Scope (already answered)

User chose: **${scopeKey}** — ${scopeLine}

## Step 2: Explore the codebase

Preferred path — invoke \`task_tool\` with:
\`\`\`
subagent_type: "explore_agent"
task_description: |
  Thoroughly explore the repository at ${rootDir}. Use "very thorough" exploration.
  Read these key files if present (use absolute paths):
    - Manifests: package.json, Cargo.toml, pyproject.toml, go.mod, pom.xml, build.gradle*, setup.py
    - Docs: README.*, CONTRIBUTING.*, ARCHITECTURE.*, docs/
    - Build/CI: Makefile, justfile, .github/workflows/*, .gitlab-ci.yml, azure-pipelines.yml
    - AI tool configs: JIUWENCLAW.md, CLAUDE.md, AGENTS.md, OPENJIUWEN.md,
                       .jiuwen/rules/*, .claude/rules/*, .cursor/rules/*,
                       .cursorrules, .github/copilot-instructions.md,
                       .windsurfrules, .clinerules, .mcp.json
    - Config: .jiuwen/settings*.json (read-only; do not rewrite)
  Detect and report back concisely:
    - Build / test / lint / format commands (especially non-standard ones)
    - Primary languages, frameworks, package manager
    - Project structure (monorepo, multi-module, single-package)
    - Code style rules differing from language defaults
    - Non-obvious gotchas, required env vars, workflow quirks
    - Branch / PR / commit message conventions
    - Run \`git -C ${rootDir} worktree list\` and mention if multiple worktrees exist
  Note anything you CANNOT figure out from code alone — these become interview questions.
\`\`\`

Fallback (no task_tool): do the same yourself with \`glob\` and \`read_file\`; focus on the manifest + README first, then Makefile / CI configs.

## Step 3: Fill gaps + build proposal

Gather info code can't answer. Use the \`ask_user\` tool with structured \`questions\` parameter.

The \`ask_user\` tool supports a \`questions\` parameter for presenting selectable options:
\`\`\`
ask_user(
  query="Brief description of what you're asking",
  questions=[
    {
      question: "The full question text",
      header: "ShortTag",
      options: [
        {label: "Option A", description: "What option A means"},
        {label: "Option B", description: "What option B means"},
      ],
      multi_select: false,
    }
  ]
)
\`\`\`

Use selectable options when they help clarify the question, or ask open-ended questions to gather free-form input. The user can always choose "Other" for custom input.

For scope \`project\` / \`both\`: ask about team practices —
  non-obvious commands, branch/PR conventions, env setup, testing quirks, common pitfalls.
  Skip items already obvious from README or manifests. Do not mark any answer as "recommended" — this is about the team's actual workflow.

For scope \`personal\` / \`both\`: ask about the user —
  role, familiarity with this codebase, sandbox URLs / accounts, communication preferences, specific tooling setup on their machine.

**Synthesize a proposal** combining Step 2 findings and Step 3 answers. Because skills and hooks are outside the current scope, ALL items become JIUWENCLAW.md notes (team) or JIUWENCLAW.local.md notes (personal). Present as a plain-text list, one line per item, grouped by target file. Ask for confirmation before proceeding.

**Build the preference queue** from the accepted proposal:
\`[{type: "note", target: "JIUWENCLAW.md" | "JIUWENCLAW.local.md", content: "..."}]\`
Steps 4–5 consume this queue.

## Step 4: Write JIUWENCLAW.md (if scope is project or both)

Target: \`${rootDir}/JIUWENCLAW.md\`

${existing.jiuwenclawMd ? "File EXISTS — read it, propose a merged diff, use `ask_user` with `questions` to get user confirmation (options: 'Apply update' / 'Skip (keep current)'), then apply via Edit if confirmed. DO NOT use Write to overwrite silently." : "File is absent — use Write to create it."}

Consume queue entries whose \`target == "JIUWENCLAW.md"\`.

**Content test**: for each candidate line, ask "Would removing this cause the assistant to make mistakes?" If no, cut.

**Include**:
- Build / test / lint / format commands the assistant can't guess
- Code style rules that deviate from language defaults
- Testing quirks (e.g., "run single test with \`pytest -k ...\`")
- Repo etiquette (branch naming, PR conventions, commit message style)
- Required env vars, setup steps
- Important parts from existing AI coding tool configs if they exist (AGENTS.md, .cursor/rules, .cursorrules, .github/copilot-instructions.md, .windsurfrules, .clinerules) — extract key rules, not just link to them
- Non-obvious gotchas, architectural decisions worth knowing
- A brief **See also** section. Use plain markdown links for short references, or \`@path/to/file\` includes when a longer source document should stay authoritative:
    ${legacyIncludesEn(existing)}

**Exclude**:
- File-by-file structure or component lists (assistant can discover)
- Standard language conventions (assistant already knows)
- Generic AI etiquette / prompt engineering advice
- Long inline reference material — link to it rather than inline
- Commands already obvious from manifests (e.g., "npm test")
- Frequently-changing information — reference the source with \`@path/to/doc.md\` so the latest version is always loaded
- Generic advice like "write clean code" or "handle errors" — only include specific, actionable rules

**Specificity rule**: "Use 2-space indentation in TypeScript" is better than "Format code properly."

**No invented sections**: Do not make up headings like "Common Development Tasks" or "Tips for Development" — only include information expressly found in files you read.

**Prefix** the file with:
\`\`\`
# JIUWENCLAW.md

This file provides guidance to JiuwenClaw (and any compatible AI coding assistant) when working with code in this repository.
\`\`\`

For monorepos: mention that subdirectory \`JIUWENCLAW.md\` is supported — ProjectMemoryRail walks up from cwd, so per-package docs are welcome.

For rule organization at team scale: suggest creating \`.jiuwen/rules/<topic>.md\` — these are auto-scanned, and may use frontmatter \`paths:\` to scope rules by the current working subtree / workspace.

## Step 5: Write JIUWENCLAW.local.md (if scope is personal or both)

Target: \`${rootDir}/JIUWENCLAW.local.md\`

${existing.jiuwenclawLocalMd ? "File EXISTS — propose additions via Edit, never overwrite." : "File is absent — use Write to create it."}

Consume queue entries whose \`target == "JIUWENCLAW.local.md"\`.

Include: user's role, familiarity, personal URLs / accounts, communication preferences, tool setup specific to the user's machine.

**After writing**, idempotently update \`${rootDir}/.gitignore\`:
  1. Read \`.gitignore\` if it exists (use absolute path).
  2. Check whether each of the two lines below is already present (exact line match).
  3. Append only the missing ones:
       - \`JIUWENCLAW.local.md\`
       - \`.jiuwen/settings.local.json\`
  4. If \`.gitignore\` does not exist, create it with those two lines.

## Step 6: Summary

Briefly recap which files were written and the 3–5 most important items in each.

Remind the user:
- These files are auto-loaded into every coding session by ProjectMemoryRail.
- They're a starting point — feel free to edit by hand; changes take effect next turn.
- Re-run \`/init\` anytime to refresh based on new findings.

Then suggest optimizations as a short checklist, only those relevant to this repo:
- If tests are missing / sparse: suggest setting up a framework so the assistant can verify its own changes.
- If no formatter / lint config was found: suggest adding one with a one-line reason.
- If Step 2 found legacy AI config files (CLAUDE.md, AGENTS.md, etc.) not referenced in JIUWENCLAW.md: suggest consolidating via plain links or follow-up cleanup.
- **Always include**: "Run \`/compact\` after reviewing to trim this init session from history."
`;
}

// ---------------------------------------------------------------------------
// Chinese
// ---------------------------------------------------------------------------

function buildInitPromptZh({ rootDir, scopeKey, existing }: BuildInitPromptArgs): string {
  const scopeLine = SCOPE_DESCRIPTION_ZH[scopeKey];
  return `为本仓库生成一份最小可用的 JIUWENCLAW.md（团队共享）与可选的 JIUWENCLAW.local.md（个人私有）。
这些文件会被 ProjectMemoryRail 自动注入到每一轮 coding 模式会话的 system prompt，因此必须**精简** —— 只写"不写就会出错"的信息。

## 关键约束（必读，不可违反）

1. **所有文件操作必须使用绝对路径，根为：\`${rootDir}\`**
   永远不要用相对路径。写入或编辑时总是构造 \`${rootDir}/<文件名>\`。
2. **禁止使用 \`coding_memory_read\` / \`coding_memory_write\` / \`coding_memory_edit\` 工具。** 那是会话级自动记忆，和 /init 是两套系统。/init 只通过文件写入工具产出静态项目文档。
3. **工作区根目录现有文件（已预探测）**：
   - JIUWENCLAW.md：${yesNoZh(existing.jiuwenclawMd)} ${existing.jiuwenclawMd ? "—— 必须先读取、生成 diff，然后用 \`ask_user\` 的 \`questions\` 参数让用户选择。示例：\`ask_user(query='更新 JIUWENCLAW.md？', questions=[{question: 'JIUWENCLAW.md 已存在，你想怎么处理？', header: '更新', options: [{label: '应用更新', description: '把提议的变更合并到现有文件'}, {label: '跳过（保留当前）', description: '保持文件不变，继续后续步骤'}], multi_select: false}])\`。若用户选「应用更新」，用 Edit 执行 diff；若选「跳过」，保持文件不变继续。严禁静默覆盖。" : ""}
   - JIUWENCLAW.local.md：${yesNoZh(existing.jiuwenclawLocalMd)} ${existing.jiuwenclawLocalMd ? "— 只能通过 Edit 追加，不要覆盖。" : ""}
   - 遗留参考文件（不要删改，可用 markdown 链接引用）：CLAUDE.md=${yesNoZh(existing.claudeMd)}, CLAUDE.local.md=${yesNoZh(existing.claudeLocalMd)}, AGENTS.md=${yesNoZh(existing.agentsMd)}, OPENJIUWEN.md=${yesNoZh(existing.openjiuwenMd)}, .cursorrules=${yesNoZh(existing.cursorRules)}, .github/copilot-instructions.md=${yesNoZh(existing.copilotInstructions)}
4. **子代理 bash 命令必须加前缀**：\`cd ${rootDir} && ...\` 或用 \`git -C ${rootDir}\`，因为子代理的 CWD 不保证等于 \`${rootDir}\`。
5. **只要可用，始终优先使用 \`task_tool\` 且 \`subagent_type: "explore_agent"\`。** 若本轮工具列表里没有 \`task_tool\`，就静默降级为用 \`glob\` / \`grep\` / \`read_file\` / \`bash\` 自行探索。
6. **默认只发起一次 \`task_tool\` / \`explore_agent\` 调用。** 若仓库明显较大、为 monorepo，或单次探索信息不足，可按需拆分多个 explore 子代理；只有在确有收益时才并发，避免重复扫描与结果合并噪音。

## 步骤 1：范围（已确定）

用户选择：**${scopeKey}** — ${scopeLine}

## 步骤 2：探索代码库

首选：调用 \`task_tool\`，参数：
\`\`\`
subagent_type: "explore_agent"
task_description: |
  彻底探索仓库 ${rootDir}，请求 "very thorough" 级别。
  若存在请读取（用绝对路径）：
    - 清单：package.json, Cargo.toml, pyproject.toml, go.mod, pom.xml, build.gradle*, setup.py
    - 文档：README.*, CONTRIBUTING.*, ARCHITECTURE.*, docs/
    - 构建/CI：Makefile, justfile, .github/workflows/*, .gitlab-ci.yml, azure-pipelines.yml
    - AI 配置：JIUWENCLAW.md, CLAUDE.md, AGENTS.md, OPENJIUWEN.md,
              .jiuwen/rules/*, .claude/rules/*, .cursor/rules/*,
              .cursorrules, .github/copilot-instructions.md,
              .windsurfrules, .clinerules, .mcp.json
    - 配置：.jiuwen/settings*.json（只读，不要重写）
  简洁地汇报以下内容：
    - 构建/测试/lint/format 命令（特别是非标准的）
    - 主要语言、框架、包管理器
    - 项目结构（monorepo / 多模块 / 单包）
    - 与语言默认不同的代码风格规则
    - 不易察觉的坑、必需环境变量、工作流习惯
    - 分支 / PR / commit message 约定
    - 执行 \`git -C ${rootDir} worktree list\`，若有多 worktree 请说明
  对于从代码无法推断的问题，记录下来作为后续的访谈问题。
\`\`\`

无 task_tool 时的兜底：用 \`glob\` + \`read_file\` 自己做同样的事，先看清单和 README，再看 Makefile / CI 配置。

## 步骤 3：补齐信息 + 生成提案

收集代码无法回答的问题。用 \`ask_user\` 工具的 \`questions\` 参数提供可选项：

\`\`\`
ask_user(
  query="简要说明你在问什么",
  questions=[
    {
      question: "完整的问题文本",
      header: "短标签",
      options: [
        {label: "选项 A", description: "选项 A 的含义"},
        {label: "选项 B", description: "选项 B 的含义"},
      ],
      multi_select: false,
    }
  ]
)
\`\`\`

根据问题性质选择选项式提问或直接输入式提问；用户始终可以选择「其他」进行自定义输入。

对 \`project\` / \`both\` 范围：询问团队实践 —
  非显而易见的命令、分支 / PR 约定、环境初始化、测试习惯、常见坑位。
  README 或清单里已经写清楚的就别问。**不要**给任何选项标记"推荐" —— 这是团队实际做法，不是建议。

对 \`personal\` / \`both\` 范围：询问用户 —
  角色、对本仓库的熟悉度、沙箱 URL / 账号、沟通偏好、本机工具链特殊设置。

**合成提案**：把步骤 2 的发现和步骤 3 的回答整合。当前方案不支持 Skills 和 Hooks，所有条目一律归为 JIUWENCLAW.md（团队）或 JIUWENCLAW.local.md（个人）的记录项。用纯文本列表呈现，按目标文件分组。请求用户确认后再写文件。

**构造偏好队列**：
\`[{type: "note", target: "JIUWENCLAW.md" | "JIUWENCLAW.local.md", content: "..."}]\`
后续写文件步骤会消费此队列。

## 步骤 4：写 JIUWENCLAW.md（当范围是 project 或 both）

目标：\`${rootDir}/JIUWENCLAW.md\`

${existing.jiuwenclawMd ? "文件已存在 —— 先读取，生成合并 diff，用 \`ask_user\` 的 \`questions\` 参数获取用户确认（选项：「应用更新」 / 「跳过（保留当前）」），确认后用 Edit 应用。绝不要用 Write 静默覆盖。" : "文件不存在 —— 用 Write 创建。"}

消费队列中 \`target == "JIUWENCLAW.md"\` 的条目。

**内容筛选测试**：对每行候选，自问"去掉这行会不会让助手犯错？" 不会就删掉。

**应包含**：
- 助手猜不出的构建 / 测试 / lint / format 命令
- 偏离语言默认的代码风格规则
- 测试习惯（例如"用 \`pytest -k 'x'\` 跑单测"）
- 仓库规矩（分支命名、PR 约定、commit message 风格）
- 必需环境变量、初始化步骤
- 从已有的 AI 工具配置文件中提取重要内容（CLAUDE.md、AGENTS.md、.cursorrules、.github/copilot-instructions.md、.windsurfrules、.clinerules 等） —— 提取关键规则，而非只留链接引用
- 不易察觉的坑、值得知道的架构决策
- 简短的 **See also** 段落。短引用可用普通 markdown 链接；若希望保留长文档作为权威来源，可用 \`@path/to/file\` 引用：
    ${legacyIncludesZh(existing)}

**不应包含**：
- 逐文件 / 逐组件的结构清单（助手可以自己发现）
- 语言的标准约定（助手已经知道）
- 通用 AI 礼仪 / prompt 工程建议
- 长篇参考材料 —— 用链接引用而非内联
- 清单中显而易见的命令（比如"npm test"）
- 频繁变化的信息 —— 用 \`@path/to/doc.md\` 引用源头，确保每次加载的都是最新版本
- 通用建议如"写干净代码"或"处理好错误" —— 只写具体、可执行的规则

**具体性原则**："TypeScript 用 2 空格缩进"比"代码要格式规范"好。

**禁止虚构段落**：不要自创"常见开发任务"或"开发技巧"之类的标题 —— 只收录你从文件中实际读到的信息。

**文件开头**统一加：
\`\`\`
# JIUWENCLAW.md

This file provides guidance to JiuwenClaw (and any compatible AI coding assistant) when working with code in this repository.
\`\`\`

对 monorepo：说明支持子目录放独立的 \`JIUWENCLAW.md\` —— ProjectMemoryRail 从 cwd 向上遍历加载。

对团队规模较大的项目：建议把按主题拆分的规则放到 \`.jiuwen/rules/<topic>.md\` —— 当前运行时会自动加载这些规则，并支持用 \`paths:\` frontmatter 按当前工作目录 / workspace 所在子树限定作用域。

## 步骤 5：写 JIUWENCLAW.local.md（当范围是 personal 或 both）

目标：\`${rootDir}/JIUWENCLAW.local.md\`

${existing.jiuwenclawLocalMd ? "文件已存在 —— 通过 Edit 追加内容，不要覆盖。" : "文件不存在 —— 用 Write 创建。"}

消费队列中 \`target == "JIUWENCLAW.local.md"\` 的条目。

包含：用户的角色、对仓库的熟悉程度、个人 URL / 账号、沟通偏好、本机特有工具链配置。

**写完后幂等更新** \`${rootDir}/.gitignore\`：
  1. 若 \`.gitignore\` 存在先读取（用绝对路径）；
  2. 检查下面两行是否已存在（整行精确匹配）；
  3. 仅追加缺失的：
       - \`JIUWENCLAW.local.md\`
       - \`.jiuwen/settings.local.json\`
  4. 若 \`.gitignore\` 不存在，就创建并写入这两行。

## 步骤 6：总结

简要回顾写了哪些文件，每个文件里 3-5 条最重要的内容。

提醒用户：
- 这些文件会被 ProjectMemoryRail 自动加载到每一轮 coding 会话。
- 是起点 —— 可以手工编辑，下一轮就生效。
- 随时可以再跑 \`/init\` 基于新发现重新生成。

然后给一个短清单（只写与当前仓库相关的）：
- 若测试缺失 / 稀疏：建议引入测试框架，助手才能自证修改。
- 若没有 formatter / lint 配置：建议添加，并说明一行理由。
- 若步骤 2 发现了 JIUWENCLAW.md 中未引用的遗留 AI 配置文件（CLAUDE.md、AGENTS.md 等）：建议以普通链接方式提示用户后续合并。
- **总是包含**："检查完后运行 \`/compact\` 可把这段初始化会话从历史中精简掉。"
`;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SCOPE_DESCRIPTION_EN: Record<ScopeKey, string> = {
  project: "write only JIUWENCLAW.md (run Step 4).",
  personal: "write only JIUWENCLAW.local.md (run Step 5).",
  both: "write both files (run Step 4 and Step 5).",
};

const SCOPE_DESCRIPTION_ZH: Record<ScopeKey, string> = {
  project: "只写 JIUWENCLAW.md（执行步骤 4）。",
  personal: "只写 JIUWENCLAW.local.md（执行步骤 5）。",
  both: "两份都写（步骤 4 和步骤 5 都执行）。",
};

function yesNo(b: boolean): string {
  return b ? "EXISTS" : "absent";
}

function yesNoZh(b: boolean): string {
  return b ? "存在" : "不存在";
}

function legacyIncludesEn(existing: ExistingFiles): string {
  // 当前方案：不用 @path 展开；写普通 markdown 链接
  const parts: string[] = [];
  if (existing.claudeMd) parts.push("[CLAUDE.md](./CLAUDE.md)");
  if (existing.agentsMd) parts.push("[AGENTS.md](./AGENTS.md)");
  if (existing.openjiuwenMd) parts.push("[OPENJIUWEN.md](./OPENJIUWEN.md)");
  if (existing.cursorRules) parts.push("[.cursorrules](./.cursorrules)");
  if (existing.copilotInstructions)
    parts.push(
      "[.github/copilot-instructions.md](./.github/copilot-instructions.md)",
    );
  return parts.length
    ? `"See also: ${parts.join(", ")}."`
    : `"(No legacy AI config files detected.)"`;
}

function legacyIncludesZh(existing: ExistingFiles): string {
  const parts: string[] = [];
  if (existing.claudeMd) parts.push("[CLAUDE.md](./CLAUDE.md)");
  if (existing.agentsMd) parts.push("[AGENTS.md](./AGENTS.md)");
  if (existing.openjiuwenMd) parts.push("[OPENJIUWEN.md](./OPENJIUWEN.md)");
  if (existing.cursorRules) parts.push("[.cursorrules](./.cursorrules)");
  if (existing.copilotInstructions)
    parts.push(
      "[.github/copilot-instructions.md](./.github/copilot-instructions.md)",
    );
  return parts.length
    ? `"另见：${parts.join("、")}。"`
    : `"（未探测到遗留 AI 配置文件。）"`;
}
