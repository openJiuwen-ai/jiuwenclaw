# 基准提交（parent_sha）选择

Bench 分支必须停在 **修复合入之前** 的代码状态，Agent 才能在该分支上复现 Bug 并验证修复。

## 默认规则

1. 从 **upstream**（只读 API）PR 的 `commits` 列表取 **最后一个** commit 作为 `fix_sha`（修复提交）。
2. 调用 `GET /repos/{owner}/{repo}/commits/{fix_sha}`，取 **`parents[0]`** 作为 `parent_sha`。
3. 在 `parent_sha` 上创建 `bench-issue-N`。

这与「在修复 PR 的父提交上 checkout」等价。

## 何时覆盖

| 情况 | 做法 |
|------|------|
| Squash merge，PR 列表只有 1 个 commit | 仍用该 commit 为 fix；parent 来自 API parents |
| PR 含多个逻辑提交，修复在**中间** | `--fix-sha` 指定真正的修复 commit |
| parents 为空或 API 异常 | `--parent-sha` 手动指定；或取 commits 列表中 fix 的**前一个** |
| 修复跨多个 commit | 与用户确认：bench 基点应早于**最早**引入修复的 commit |

## 常见错误

- ❌ 在 `fix_sha` 上建分支 → 已是修复后代码，无法复现  
- ❌ 使用 `main`/`develop` 最新 HEAD → 可能已包含其它无关提交  
- ❌ 未 fetch upstream 就建分支 → 本地无对象或 SHA 不对  
- ❌ `git push upstream bench-issue-N` 或 Issue 建在 upstream → bench 题发错仓，须在 fork 重做  

## 验证清单

- [ ] `git log -1` 显示为 `parent_sha` 短 SHA  
- [ ] PR files 中某行「修复前」代码在本地文件中可见  
- [ ] `git status` 无未提交改动  
