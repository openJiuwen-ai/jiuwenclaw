# Sample Expert Group

Team 模式 `agent_group` 的内置最小示例：一个 Leader、两个预定义 Member 和一个共享 Skill。每个 Agent manifest 使用顶层 `name`、`description`，运行时 ID 由目录名派生。Leader 使用 `AGENT.md` 与 persona；普通 Member 的职责写入 persona，不包含 `AGENT.md`。团队以 `hybrid` 模式运行，后续仍可动态增加成员。
