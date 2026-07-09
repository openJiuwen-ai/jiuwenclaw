# 跨平台提问工具

**问题内容、选项、单题分轮、记录格式** 各平台相同；仅工具名与 Leader 入口路径不同。

## 平台对照

| 平台 | Leader 入口 | 结构化提问 |
|------|-------------|------------|
| **Claude Code** | `.claude/skills/dev-leader` | `AskUserQuestion`（如 `.claude/hooks/notify.py` 弹窗） |
| **Cursor** | Agent + `dev-leader` skill | `AskQuestion` |
| **Codex** | `.codex/skills/dev-leader` | 会话提供的 `AskUserQuestion` 或等价工具 |
| **OpenCode** | `.opencode/skills/dev-leader` | `AskUserQuestion` / `AskQuestion` 或等价 |
| **jiuwenswarm** | `~/.jiuwenswarm/.../dev-leader` | 同宿主 IDE/CLI |

## 判定顺序（通用）

1. 查看本会话工具列表是否含 `AskUserQuestion`、`AskQuestion` 或文档明示等价名。  
2. **有** → 调用（`questions` 长度 1）；选项见 [tool-payload.md](tool-payload.md)；调用后 **结束本回合**。  
3. **无** 或 **not found** → [fallback-chat.md](fallback-chat.md)。  
4. 弹窗关闭 / payload 错误 / 超时 → 修正后重试（≤2 次），再 fallback。

## 呈现（卡片 / IM）

飞书、企业 IM 等：**一问题一卡片**。禁止一次 `questions` 多项指望通道拆成 `(1/N)` 多卡；桌面弹窗虽可多题，Aidlc 仍 **每次工具调用仅一题**。

## 常见误用

| 误用 | 正确 |
|------|------|
| 正文「请回复 A/B/C，我暂停等待」 | 调用 `AskUserQuestion` / `AskQuestion` |
| 因用户需思考而绕过工具 | 仍用工具；思考发生在工具交互内 |
| 参数错误后直接 fallback | 修正 question/选项 后重试 |

## 路径

各平台 `dev-leader` 引用仓库根 `skills/user-interact/`。仅复制 `.opencode/` 等而未带 `skills/` 会导致 Leader 无法加载本 skill。`skills_root` → `aidlc-common/references/skills-paths.md`；G0 写入任务卡并随 spawn 附带。
