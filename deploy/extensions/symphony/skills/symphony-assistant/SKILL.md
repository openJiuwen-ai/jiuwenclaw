---
name: symphony-assistant
description: Must use first when the user asks to use skill(s) or 技能 to complete a task.
allowed_tools:
  - symphony_compose_score
  - symphony_read_score
  - symphony_refresh_score
---

# Symphony Assistant

Use this skill when a task should be completed by selecting, combining, or ordering currently installed skills.

## Workflow

1. If the user says to use skill(s) or 技能, always call `symphony_compose_score` with the original user task as `query`.
2. Do not manually list skill folders, list skill names, or choose a skill chain before calling `symphony_compose_score`.
3. Treat `symphony_compose_score` as the only planning entrypoint: it reads the Symphony score, refreshes missing or stale scores, and returns the Mermaid/Markdown execution graph.
4. The `symphony_compose_score` result may already be displayed directly to the user; otherwise, present its returned `content` or `markdown` directly.
5. Treat the returned plan, Mermaid graph, missing inputs, and caveats as the source of truth.
6. Do not call individual skill tools just to manually recreate or verify the Symphony plan.
7. If Symphony reports missing inputs, ask the user for those inputs instead of inventing them.
8. For ordinary tasks that do not ask to use installed skills, do not use Symphony.

## Notes

- `symphony_compose_score` reads the current Symphony score, refreshes it when missing or stale, and then plans the skill execution graph.
- Use `symphony_read_score` and `symphony_refresh_score` directly only when the user explicitly asks to read or refresh the Symphony score.
