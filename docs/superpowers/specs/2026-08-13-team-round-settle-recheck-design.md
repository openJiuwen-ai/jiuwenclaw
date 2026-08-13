# 团队轮次收尾复评（settle recheck）设计

日期：2026-08-13
状态：已确认根因，待实现
涉及仓：jiuwenswarm（jiuwenclaw/agentserver/deep_agent/team_helpers.py）

## 背景与根因

2026-08-13 事故现场（同事机器日志 `D:\tmp\bug-0812\bug-0813\bug-0813`）：

- 对话1（session cce1ef0d）round 2：六个任务全部终态（09:03:53 `task list drained`），
  成员 office 09:04:24.633 落定（READY），但 leader 的 final 在 ~09:04:24.5 发出，
  **比成员落定早约 60ms**。`team_helpers.py:2628` 的收尾判定是"leader final 那一瞬间
  检查团队是否 settled"的一次性点判定——判定失败后再无任何复评触发，流挂死 9.5 分钟，
  用户手动停止。round 3 同法死于 240s idle-stall 拆流。
- 对话2（session 40e6544a）：leader 口头派活（零任务），成员以普通文本结束轮次
  （成员无 ask_user_question 工具，by design leader-only）。leader final 更早
  （08:53:16），点判定必然 miss；openjiuwen 侧 `is_team_completed()` 又硬性要求
  "至少一张工单"（`tools/team.py:1009` `if not tasks: return None`），周期 POLL_TASK
  兜底路被堵死。240s 后 idle-stall 拆流 → relay 合成
  "团队回合长时间未响应，已自动停止（空闲超时）"（errorCode=team_stalled）→
  前端 failRun + 错误气泡。

**根因一句话**：轮次收尾只在 leader final 的瞬间尝试一次，错过即成死锁；
两条本可兜底的路径（复评触发、零任务收尾）都不存在。

## 方案对比

- **A（选中）jiuwenclaw 复评收尾**：见过 leader final 后，空转 tick 上复评 settle，
  settled 即正常收尾。一处改动，两个事故现场同时覆盖（`_team_round_settled` 本就接受
  零任务空列表），不依赖上游发版。
- B openjiuwen 上游修复（成员 settle 触发 leader 重估 + 零任务语义）：治本但需上游
  配合，周期长。作为后续跟进项，不阻塞本次。
- C relay 层规避：relay 拿不到 settle 状态，只能做症状层兜底，排除。

## 改动设计（team_helpers.py `_consume_stream_with_query_impl` 内）

1. 新增 `leader_final_seen` 标志：`chat.final` 且 `is_leader` 的分支内置真
   （即现有 2628 点判定处）。
2. 新增常量 `_TEAM_STREAM_SETTLE_RECHECK_S = 2.0`：leader_final_seen 之后，
   `asyncio.wait_for` 的超时从 `_TEAM_STREAM_IDLE_BREAK_S`（240s）缩短为 2s 轮询。
3. 新增 `idle_since_last_chunk` 单调时钟（循环入口初始化，每收到 chunk 重置），
   用于 240s 预算判断；与现有 `last_relay_business_at`（relay 业务帧计时）不混用。
4. `asyncio.TimeoutError` 分支新顺序：
   - `leader_final_seen` 为真时：
     a. `_team_has_pending_user_decision` 为真 → 继续等（等用户决策不封顶，语义照旧）；
     b. 复评 `_team_round_settled` 为真 → log + `break`（干净收尾：finally 里已有的
        settled 复判会发 `team.completed`，无需额外动作）；
     c. 未 settled 且 `now - idle_since_last_chunk < _TEAM_STREAM_IDLE_BREAK_S`
        → `continue` 等下一个 2s tick；
     d. 超过预算 → 落入原有 pending/busy/teardown 决策树（busy defer 及其封顶语义不变）。
   - `leader_final_seen` 为假：原有逻辑一行不动。
5. 诊断日志：预算耗尽且 `leader_final_seen` 但复评仍不真时，WARNING 级打印 monitor
   快照关键字段（成员状态/任务状态/未读标记），供下次定位（覆盖"未读消息污染"等
   目前只能推测的场景）。

## 行为兼容性

| 场景 | 现状 | 改后 |
|---|---|---|
| 未见 leader final 的流 | 240s → pending/busy/teardown | 不变 |
| leader final 后顺利 settled | 240s 后拆流报错（team_stalled） | settled 后 ≤2s 干净收尾 + team.completed |
| leader final 后永不 settled | 240s 拆流报错 | 不变（多一条诊断 WARNING） |
| pending 用户决策 | 不封顶等待 | 不变 |

## 测试计划（TDD，先写失败测试）

新增 `tests/unit_tests/agentserver/test_team_settle_recheck.py`
（复用 test_team_stream_idle_break.py 的 _FakeStream/_patch_common 工装）：

- T1 竞态复现（对话1）：leader chat.final chunk 后流阻塞；`_team_round_settled`
  序列 [False（final 点判定）, True（复评）] → 断言：发出 team.completed、
  无 team.stalled、总时长远小于 idle 预算。
- T2 零任务同构（对话2）：final 后 settled 延迟多个 recheck tick 才为真 → 干净收尾。
- T3 回归：final 后永不 settled → 超预算 → team.stalled 照旧发出。
- T4：final 后 pending 用户决策 → 不收尾不拆流；pending 解除且 settled → 干净收尾。
- 回归：test_team_stream_idle_break.py、test_team_direct_answer_finish.py 全量，
  以及 agentserver 单测全量。

## 后续项（不在本次范围）

- openjiuwen 上游：成员 settle 触发 leader 完成度重估；`is_team_completed` 零任务语义。
- relay 对 idle-stall teardown 的收尾处理不一致（对话1 round 3 无合成错误、无
  request complete 日志、草稿未闭环 vs 对话2 正常合成 team_stalled 错误）。
