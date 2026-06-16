# Agentic Skill Retrieval
---

## Concepts

### What is Agentic Skill Retrieval?

Agentic Skill Retrieval is JiuwenSwarm's skill-directory retrieval feature for environments with a **large number of installed skills**.

When only a few skills are installed, the agent can often inspect the list directly. When dozens or hundreds of skills are installed, injecting every skill into the prompt causes two common problems:

| Problem | Impact |
|---------|--------|
| High context usage | User tasks, files, and conversation history have less room |
| Attention dilution | The model may focus on irrelevant skills and miss the right one |

Agentic Skill Retrieval builds a local **installed-skill tree index** and exposes directory-browsing tools at runtime. The agent can inspect likely branches step by step, then decide which `SKILL.md` files to read.

### What it is for

Use it when:

- Many skills are installed and full skill injection would be noisy.
- A task may require multiple capability areas, such as PDF reading, table extraction, and report generation.
- The user does not name a skill, but installed skills may help.
- In Team / Cluster Mode, the Leader needs to identify relevant skills before dispatching work.

It is not meant for:

- Very small skill sets where listing all skills is enough.
- Tasks where the user already names the exact skill to use.
- Tasks that do not need installed skills.
- Installing new skills. Agentic Skill Retrieval only searches **installed skills**.

### Basic workflow

```text
Install skills
  ↓
Build local skill tree index
  ↓
User sends a task
  ↓
Agent chooses likely skill-tree branches
  ↓
skill_branch_explore expands relevant branches
  ↓
skill_branch_peek checks summaries when needed
  ↓
Candidate skills appear
  ↓
Agent reads relevant SKILL.md files
  ↓
JiuwenSwarm executes the task with its existing runtime
```

Agentic Skill Retrieval only helps find skills. It does not replace JiuwenSwarm's existing execution, file operations, tool calls, or team dispatch logic.

### Runtime tools

When enabled, the agent receives these tools:

| Tool | Purpose | When to use |
|------|---------|-------------|
| `skill_branch_explore` | Expand skill-tree branch nodes and reveal child branches or candidate skills | Main retrieval tool |
| `skill_branch_peek` | Inspect lightweight child-branch summaries without expanding the tree | Use when unsure whether a branch is worth exploring |
| `skill_index_build` | Build or reuse the local skill-tree index | Use only when retrieval tools report a missing or stale index |

In normal use, the agent should call `skill_branch_explore` or `skill_branch_peek` first. It should call `skill_index_build` only when the returned result explicitly says the index is missing or stale.

### Chat visualization

Within one conversation turn, the model may call `skill_branch_explore` and `skill_branch_peek` multiple times. The Web UI groups those calls into one dynamic skill tree:

- `explore` expands a node's child branches or skills.
- `peek` adds lightweight summaries to a node without expanding it.
- Multiple calls update the same tree instead of producing unrelated tool cards.
- Candidate skills appear as terminal nodes so users can see why the agent selected them.

This makes the retrieval process visible: users can follow how the agent moves from top-level categories to concrete skills.

---

## Operation Guide

### 1. Install skills

Agentic Skill Retrieval only searches installed skills. Install the skills you need from the left sidebar **Skills** page first.

See [Skills](Skills.md) for installation methods.

### 2. Enable Agentic Skill Retrieval

Open:

```text
Left sidebar → Configuration → Agentic Skill Retrieval
```

Enable **Skill Retrieval** and save the configuration.

When the switch is disabled, JiuwenSwarm does not register the retrieval tools or inject skill-tree guidance. The system returns to the original skill workflow.

### 3. Build the skill index

Open:

```text
Left sidebar → Skills → Skill Index → Build Index
```

The build scans installed skills, reads names, descriptions, and `SKILL.md`, then writes a local skill-tree index. After a successful build, the same index is reused; it does not need to be rebuilt on every startup.

Rebuild when:

- Skills were installed, uninstalled, or heavily modified.
- The UI reports that the index is missing, stale, or failed.
- Build settings such as root categories or max tree depth changed.

### 4. Use retrieval in chat

Users usually do not call the tools directly. Send a normal task, for example:

```text
Please prioritize currently installed skills for this task. If relevant skills are found, use their contents in your answer.

I have a PDF contract and an Excel spreadsheet. Extract key clauses, verify amount fields, and generate a Chinese review report.
```

With Agentic Skill Retrieval enabled, the model can browse the skill tree, discover PDF, Excel, document review, or report-generation skills, and then decide which `SKILL.md` files to read.

### 5. Inspect the retrieval process

In the chat message, expand the skill retrieval tree to see:

- Which top-level categories the model inspected.
- Which branches were peeked.
- Which branches were explored.
- Which candidate skills appeared.

When a skill looks relevant, the agent may read its `SKILL.md` before executing the task.

---

## Configuration

Common settings are available under **Configuration → Agentic Skill Retrieval**.

| Setting | Description |
|---------|-------------|
| Enable Skill Retrieval | Global switch. Disabled means the original skill flow is used |
| Root Categories | Top-level taxonomy used to guide skill-tree construction |
| Max Tree Depth | Maximum depth of the skill tree |
| Split Threshold Base | Threshold for splitting overly large branches |
| Build Workers | Build concurrency |
| Build Retries | Retry count for LLM classification or branch generation failures |
| Build Request Timeout | Timeout for a single build request |
| Build Total Timeout | Maximum total build duration; `0` means unlimited |
| Classification Batch Limit | Max skill batch size for classification |
| Build Postprocess | Clean up unclear or too-small branches after build |
| Equivalent Branch Merge | Merge semantically duplicate branches |
| Max Exposure Depth | How deep `skill_branch_explore` reveals in one call |
| Compact Codes | Use more compact node codes |
| Flatten Tree | Flatten the retrieval tree |

The default retrieval mode is non-compact and non-flat, preserving clear tree structure and branch semantics.

### Root categories

Root categories define the first layer of the tree. They help route a large number of skills into stable, mutually understandable groups.

Good root categories should:

- Cover common user tasks.
- Be as mutually exclusive as practical.
- Use short, clear descriptions.
- Avoid specific tool, framework, or skill names unless they are real business boundaries.

The Web UI shows the default taxonomy so users can edit and save it directly.

---

## FAQ

### Why did the model not call retrieval tools?

Possible reasons:

- The task already named a specific skill.
- The task does not need installed skills.
- The index is missing and retrieval has not been triggered yet.
- The global switch is disabled.

You can make the intent explicit: "Please prioritize currently installed skills for this task."

### Why does the tool say the index is missing?

Open:

```text
Skills → Skill Index → Build Index
```

Alternatively, let the model call `skill_index_build` after a retrieval tool explicitly asks for it. Then retry the task.

### Why does the build take time?

The build reads installed skills and calls the model to generate branches and classifications. More skills and deeper trees take longer. The finished index is reused across conversations.

### Does it install new skills automatically?

No. It only searches installed skills. Install new skills from the **Skills** page.

### Does it replace Team / Cluster Mode dispatch?

No. It helps the Leader or agent find relevant skills. Task decomposition, skill reading, tool execution, and team coordination still use JiuwenSwarm's existing runtime.

---

## Related docs

- [Skills](Skills.md)
- [Configuration](Configuration.md)
- [Agent Team](AgentTeam.md)
- [中文：Agentic 技能检索](../zh/Agentic技能检索.md)
