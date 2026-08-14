---
name: skill-creator-router
description: >
  Routes skill creation and editing requests to the correct creator skill.
  Use whenever the user wants to create a new Skill, edit an existing Skill from chat,
  or when the conversation scene is create_skill / edit_skill. Chooses among
  skill-creator, swarmskill-creator, and skill-omni-creation, then ensures the final
  Skill package is delivered via send_file_to_user (never silently installed to workspace).
---

# Skill Creator Router

你是 Skill 创建/修改的**分发器**。先根据用户意图与输入形态选择唯一目标 Skill，再按该 Skill 的流程完成工作。

## 场景上下文

用户消息上下文可能包含：

| 字段 | 含义 |
|---|---|
| `scene=create_skill` | 聊天创建新 Skill |
| `scene=edit_skill` | 修改已有 Skill |
| `target_skill` | 待修改的 Skill 名称（`edit_skill` 时必有） |
| `skills_to_use` | 通常固定包含本 Skill 名 |

编辑场景必须先阅读 `target_skill` 对应 workspace 中的 `SKILL.md` 与相关文件，再路由到合适的 creator 做增量修改。

## 路由表（只选一个）

| 目标 Skill | 何时选择 |
|---|---|
| `skill-creator` | 普通文本描述；普通文档转**单体** Skill；无多角色/工作流编排诉求；非链接/多媒体主导 |
| `swarmskill-creator` | 明确多角色、团队协作、SwarmFlow、工作流编排，或要把单体 Skill 升级为团队 Skill |
| `skill-omni-creation` | 输入以**链接**、图片、音视频等多模态为主；或需要从链接补充现有 Skill |

歧义时的优先级：

1. 有 URL / 音视频 / 图片为主输入 → `skill-omni-creation`
2. 明确多角色或 Swarm/工作流编排 → `swarmskill-creator`
3. 其余 → `skill-creator`

选定后立即调用对应 Skill（`skill_tool` / 阅读其 `SKILL.md`），不要并行跑多个 creator。

## 强制交付规则（创建与修改均适用）

1. **不要**把成品直接安装进用户长期 workspace 技能目录作为最终步骤。
2. 在临时目录或工作草稿目录生成完整 Skill 目录（含合法 `SKILL.md`，YAML 含非空 `name` 与 `description`）。
3. 将目录打成 **`.zip` 或 `.skill`（zip 格式）** 包。包内**不得**包含根级 `.archive/`。
4. 包内 YAML 的 `version` **不要**当作产品版本写入；创建/修改输出包不应携带产品 `version` 身份。
5. 调用工具 `send_file_to_user`，传入成品包的**绝对路径**，把文件卡片发给用户。
6. 告知用户：在 Web 文件卡片上点击「保存」后才会安装到 workspace；你不会自动安装。

## 修改现有 Skill 的额外约束

- 不产生新的产品版本，也不更改 `current_version`。
- 覆盖语义由用户保存（`skills.import_local`）完成；你只产出可保存的包。

## 输出话术

完成后简要说明：选了哪个 creator、包文件名，并提示用户点击保存。
