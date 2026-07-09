# MUST-FIX 与 Should Fix 返工

当 reviewer `gate_verdict` 为 `REWORK`/`FAIL`、存在未关闭 MUST-FIX、或 SF 分拣后存在 **本轮必改** SF 时，**不得**进入 G7a/G7b。

## G6 REWORK 禁令（Leader）

MUST-FIX 与 **本轮必改** SF **同一返工链**；`gate_verdict` 为 `REWORK`/`FAIL` 或上述项未关闭时：

- **必须** spawn 子 agent 走 §最小质量路径；**禁止** Main 代改源码/测试/文档、自行回归、或 `git commit` 声称闭环
- **禁止**以改动小、快修、REWORK 文案等理由跳过 coder → tester → reviewer
- 仅 **延期/接受** 的 SF 不触发返工

## 分拣与改派

1. 逐条 MUST-FIX 与 **本轮必改** SF 映射到 `dev-coder` / `dev-tester` / 文档上游，写入派发材料（id、路径、验收点）
2. **禁止** coder/tester/reviewer 横向私下「算作已修复」；收口仅认 Leader 改派后的磁盘证据与 Gate

## 最小质量路径（默认）

| 改动类型 | 必经子流程 |
|----------|------------|
| 业务/实现代码、依赖、影响行为的配置 | **`dev-coder` → `dev-tester` → `dev-reviewer`** |
| 仅测试/夹具/断言（实现已 OK） | **`dev-tester` → `dev-reviewer`** |
| 仅文档/注释/格式，且 reviewer 写明「无需回归测试」及依据 | **`dev-reviewer`**（须在报告中登记） |

**coder 改动可执行制品后**，再次派 reviewer 前 **必须** 派 tester 回归（除非上表第三行且已登记）。

多条 MUST-FIX / **本轮必改** SF 若能映射到不同 `PG-*` 且 touch 范围互斥，可按 `skills/dispatch-parallel` 在 G4/G5 返工阶段内并行；单条问题、共享文件、共享 fixture 或依赖未明时默认 serial。

派发 reviewer 须注明：`MODULE`、`repo-root`、`REWORK_ROUND`、`DIFF_SCOPE`、待关闭 MUST-FIX/**本轮必改** SF id；证据目录 `doc/<module>/review/`；主报告 `doc/<module>/review.md`。

## G5 / G6 状态失效

- G5 PASS 后若出现**新代码 diff**（含 MUST-FIX/**本轮必改** SF 修复）→ G5 **失效**，须重跑 tester
- G6 PASS 后任何新代码/测试变更 → G6 **失效**，须重跑 reviewer

## 轮次与升级

- G6 返工循环 **默认最多 3 轮**；第 3 轮仍 REWORK → 标记 `BLOCKED`，暂停 G7，经 user-interact `g6-blocked`
- 用户接受 MUST-FIX/**本轮必改** SF 残留风险 → 先经 user-interact `g6-risk`，并在「折中与风险登记」**逐条**列出

---

## SHOULD-FIX 分拣（G6 必做）

reviewer 返回后、宣布 G6 PASS 或进 G7a **之前**，Leader **必须**完成 SF 分拣。

1. 从 `doc/<module>/review/result.json` 读取全部 Should Fix（含 id）
2. 对 **每条** 写入 SF 分拣表并决策：

| 决策 | 含义 | 后续 |
|------|------|------|
| **本轮必改** | 本次 PR 前应修复 | 并入返工清单，走最小质量路径 |
| **延期** | 后续迭代 | 写入折中与风险登记，**不阻塞** G6 |
| **接受** | 低影响且认可现状 | 分拣表注明理由，**不阻塞** G6 |

3. **禁止** 队友横向协商 SF 是否「算修完」

### SF 分拣表示例

```markdown
## SF 分拣（Leader → 派工）

| id | 决策 | 理由 |
|----|------|------|
| CR-010 | 本轮必改 | 缺测试覆盖关键分支 |
| CR-011 | 延期 | 命名重构，下迭代跟踪 |
| CR-012 | 接受 | 风格偏好，无功能风险 |
```

### G6 PASS（SF 部分）

- **本轮必改** SF 须已关闭，或用户书面接受残留并逐条登记
- **延期/接受** SF 须在分拣表或 Final Report 中逐条登记
- **本轮必改** SF 与 MUST-FIX **共用** 3 轮返工预算
