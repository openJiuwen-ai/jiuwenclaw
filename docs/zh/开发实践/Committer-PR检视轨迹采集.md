# Committer PR 检视轨迹采集

该能力在用户通过 `persona_id == "committer"` 的 Committer 分身执行真实 GitCode PR 检视时，自动把 openJiuwen 运行轨迹归一化为可用于后续评测与自演进的 `review_trace`。

## 采集边界

- 只有 `persona_id` 严格等于 `committer` 时注册采集 Rail。
- 轨迹中必须同时包含 GitCode PR URL、`dev-reviewer` 检视及结构化执行证据；仅在对话中提到 PR 不落盘。
- 默认只保存脱敏后的 `review_trace`，不保存 raw trajectory。
- 切换到其他 persona 时会注销 Rail，避免跨会话误采集。

## 检视轨迹字段

每条 `review_trace` 是对一次 Committer PR 检视的结构化沉淀，不记录运行过程，只保留结论性事实。字段按语义分组如下。

| 分组 | 字段 | 含义 |
|---|---|---|
| 标识 | `trace_id` / `case_id` | 本次检视的唯一 ID 与样本 ID。`case_id` 由 PR URL 生成，形如 `real_pr_<owner>_<repo>_<number>`；`trace_id` 为 `<case_id>_<execution_id>_trace`。原始轨迹中没有，由 adapter 计算 |
| | `skill` / `avatar` | 本次使用的技能与分身，固定为 `dev-reviewer` 与 `Committer`，从对话历史与 coding_task 调用推断 |
| 任务 | `task.pr_url` | 本次检视的 PR。按优先级取 context.json → 结构化 PR 元数据 → collect 命令参数 → 最新用户请求，不扫描全文 |
| | `task.coding_task_called` | 是否调用过 coding_task 工具 |
| PR 元数据 | `pr_metadata.*` | `state`、`repo`、`number`、`base_sha`、`head_sha`、`base`、`head`，来自 pr_creator.py 结果、context.json 或结构化字段 |
| 证据对齐 | `evidence_alignment.*` | 标记本轨迹证据是否能对上该 PR：`pr_url`、`base_sha`、`head_sha`、`diff_hash`、`files_hash`、`collected_at`。用于后续判断 diff 与 PR 是否一致，防止拿错 PR 的 diff 参与评测 |
| 运行步骤 | `runner_steps.*` | 检视 5 个标准步骤的完成度：`collect`、`init_review`、`resolve_positions`、`report`、`post_comments`，每个 `status` 取值 `done` / `dry_run_success` / `execute_success` / 空。从 `code_review_runner.py <子命令>` 调用识别 |
| 问题清单 | `findings[]` | 检视发现的问题，每条含 `id`、`bucket`（`must_fix` / `should_fix` / `nice_to_have`）、`location`、`position`、`position_resolved`、`comment_posted`。优先从结构化 result.json 读取，缺失才回退文本扫描 |
| GitCode API | `gitcode_api.*` | 是否调用 GitCode 评论接口、是否成功、模式（`execute` / `dry_run` / 空）、dry-run 时 `comment_id` 是否为 null |
| 最终响应 | `final_response.*` | Committer 最终回复是否报告了 API 结果（`reported_api_result`）、是否声称成功（`claimed_success`）。`discussion_summary_only` 当前恒为 `False`，为预留字段，尚未实现判断逻辑 |
| 脱敏标记 | `redaction.*` | 标记本轨迹已走脱敏流程（`secrets_removed`、`private_paths_removed`）。注意这是**流程声明**而非残留检测：生产链路恒为 `True`，表示写入前已对 token/API key/Authorization/密码/用户主目录执行屏蔽，但不保证非典型形态的敏感信息已完全清除，公开前仍需人工复核 |

几个容易被误读的点：

- `findings[].location` 仅表示源码文件和行号，**不代表** GitCode 内联评论 `position` 已解析；只有结构化结果中存在有效 `position` 时 `position_resolved` 才为 `true`，`position` 为 `null` 时必为 `false`。
- 即便 `resolve-positions` 命令执行成功，只要仍有内联 finding 的 `position` 为空，`runner_steps.resolve_positions.status` 也不标记为 `done`。
- `findings` 与 `runner_steps` 来自本轮结构化执行证据；读取历史 result.json、对话中提到 PR 等不算新检视，不会落盘。
- `gitcode_api` 如实记录本轮实际行为：未发评论即记为 `called=false`、`post_comments.status` 为空，采集器不强制 dry-run，也不因 execute 改写事实。

下面是一份精简后的字段示例（实际 `findings` 与 `pr_metadata` 字段会更多）：

```json
{
  "trace_id": "real_pr_openJiuwen_agent-core_1992_<execution_id>_trace",
  "case_id": "real_pr_openJiuwen_agent-core_1992",
  "skill": "dev-reviewer",
  "avatar": "Committer",
  "task": {
    "scope": "open_pr_line_review",
    "pr_url": "https://gitcode.com/openJiuwen/agent-core/pull/1992",
    "coding_task_called": false
  },
  "runner_steps": {
    "collect": {"status": "done"},
    "init_review": {"status": "done"},
    "resolve_positions": {"status": ""},
    "report": {"status": "done"},
    "post_comments": {"status": ""}
  },
  "findings": [
    {"id": "MF-001", "bucket": "must_fix", "location": "a.py:42",
     "position": 5, "position_resolved": true, "comment_posted": false},
    {"id": "SF-1", "bucket": "should_fix", "location": "b.py:10",
     "position": null, "position_resolved": false, "comment_posted": false}
  ],
  "gitcode_api": {
    "called": false, "success": false, "mode": "",
    "dry_run": false, "execute_used": false, "dry_run_comment_ids_null": false
  },
  "final_response": {
    "reported_api_result": false,
    "discussion_summary_only": false,
    "claimed_success": false
  },
  "redaction": {"secrets_removed": true, "private_paths_removed": true}
}
```

## 保存位置

默认目录为：

```text
~/.jiuwenavatar/review_traces/<avatar_id>/default/review_traces/*.json
```

这是运行数据目录，不属于 Git 工作区。需要沉淀为训练 case 时，应经过筛选、复核和进一步脱敏，再复制到自演进数据集；不要直接把个人运行目录提交到仓库。

## 配置

- `COMMITTER_REVIEW_TRACE_ENABLED=false`：关闭采集，默认开启。
- `COMMITTER_REVIEW_TRACE_DIR=<path>`：覆盖运行数据根目录。
- `COMMITTER_REVIEW_TRACE_KEEP_RAW=true`：调试时额外保存 raw trajectory，默认关闭。注意脱敏在 raw 与 review_trace 落盘前统一执行，因此开启后落盘的 raw trajectory 仍是脱敏后的，不会写入明文密钥或用户主目录。

旧的 `COMMITTER_EVOLUTION_TRAJECTORY_*` 环境变量暂时保留兼容，但新部署应使用以上名称。

## 数据安全

写入前会递归屏蔽 token、API key、Authorization、密码等常见凭据，并把当前用户主目录替换为 `<USER_HOME>`。脱敏是风险收敛措施，不代表轨迹可未经人工复核直接公开。
