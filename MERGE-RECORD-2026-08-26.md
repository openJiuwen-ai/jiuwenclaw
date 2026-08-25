# 合并记录：origin/xiaoyi_0.2.4.beta3 → 本地 xiaoyi_0.2.4.beta3

- 日期：2026-08-26
- 合并提交：`d6ac4d742`（Merge remote-tracking branch 'origin/xiaoyi_0.2.4.beta3'）
- 合并前本地基线：`3512c7f20`（桌面集成改动提交，本身 ahead 16）
- 合并远端点：`89d3f226c`（远端 13 个新提交）
- 三方合并 base：`9c4147694`

## 一、本地保留的改造（合并的前提）

合并前先把工作区未提交的桌面客户端集成改动整体提交为 `3512c7f20`，包括：

- **命名管道/stdin 通信迁移**：E2A stdio 长度前缀帧（`server/e2a_transports.py`、
  `server/e2a_desktop.py`），`np://` 命名管道分流（`common/np_transport.py`），
  web 管道通道、专家仓库/xiaoyi 中转/模型代理等全部走 `\\.\pipe\claw-*`
- **stdin 首帧密钥包**（`common/secrets_bootstrap.py`）：密钥不再经 env/命令行下发
- **本地代理鉴权**（`common/local_proxy_auth.py`）、`llm_np_patch.py`
- **xiaoyi clientVariables** workspace/permission/modelName + 审批桥接
  （`xiaoyi_connect.py`），权限档位补丁 `update_permission_profile_in_config`
  （`common/config.py`）+ gateway 热重载回调（`app_gateway.py`）
- **skills.toggle 扇出刷新**（`agent_adapter/interface.py` 的 10 行 +
  `interface_deep.py` 的 `refresh_skill_rails_for_all_sessions`）
- 配套单测 8 个新文件（np_transport / secrets_bootstrap / local_proxy_auth /
  e2a_transports / web_pipe_channel / xiaoyi_relay_pipe / xiaoyi_file_upload_pipe 等）

## 二、远端合入的 13 个提交

```
89d3f226c feat: 增加创意设计模式、增加云端视频生成skill调用
7ad72cb2b feat(invoke): 新增统一工具 invoke，默认开启云插件与远程 Agent 调用
c0a42b576 feat(safe): 增加四个预置skill：aigc_marker, execution-validator-skill, secret-guardian, skill-scope
4606dcd09 feat(expert): 新增专家团(agent_group)包与团队线冷装
95e3e3ef1 fix: 沙箱拦截连接器命令
176475835 fix(desktop): 修复找不到正确的workspace
e8dbcb039 fix(tools): todo tool display name shows concrete task content instead of bare index
91392eb7e fix: 修复子代理审批卡片显示异常
aeca2418b fix: 修复部分工具不可用问题
8a09b0204 feat(agent): rewind 重建保留 tool_call/tool_result，history 补充 model/api_type/usage
62d3f4453 fix(agent): 去掉 broken 的 rewind 路径，统一走 attach_output+send_input
53be01147 fix(interrupt): expose structured tool_name/skill_name/tool_args in approval questions
b460bb3d2 feat(cspl): 开启CSPL风控
```

## 三、冲突与解决（共 2 个文件）

### 1. `jiuwenswarm/server/runtime/agent_adapter/interface.py`（全文件冲突）

- **冲突形态**：本地仅 +10 行；远端重写了几乎整个文件（+3160/-3123，大规模重构），
  git 无法做 hunk 级对齐，整文件成冲突。
- **解决**：以远端版为底（`checkout --theirs`），把本地的 10 行 skills.toggle 扇出
  刷新补丁**重放到远端版的对应位置**（`if _reload_after_skills:` 块内、
  `_refresh_team_shared_skill_links(request.session_id)` 之后；该区域两侧结构一致）。
- **依赖核验**：补丁调用的 `refresh_skill_rails_for_all_sessions` 是本地加在
  `interface_deep.py` 的方法，远端没有；`interface_deep.py` 自动合并保留了该方法
  （合并树 4725 行，已确认存在）。补丁用 `getattr(..., None)` + `callable` 判空，
  即便方法缺失也只是跳过扇出，不会炸。
- **结果**：远端重构全部保留 + 本地启停即时生效能力保留。

### 2. `tests/unit_tests/agentserver/test_expert_source.py`（尾部追加型冲突）

- **冲突形态**：双方在同一位置各自追加测试，互斥而非重叠——
  - 本地（HEAD）：`TestRepoClientShape` + `TestHttpRepoOverNamedPipe`
    （专家仓库 np:// 管道 transport 的单测，桌面命名管道迁移配套）
  - 远端：3 个 expert_type metadata 测试函数（专家团 4606dcd09 配套）
- **解决**：两侧全部保留（本地块在前、远端块在后），仅删除冲突标记。
- **结果**：合并后该文件测试全部通过。

## 四、合并后额外修复（1 个合并引入的测试失败）

`tests/unit_tests/test_app_agentserver.py::test_run_does_not_delete_agent_teams_directory`：

- **原因**：本地改造给 `_run` 增加了 `start_desktop_e2a_channels(server.run_connection, …)`
  与 `server.start(listen_tcp=…)`；远端该测试的 `_FakeServer` 只有无参 `start/stop`，
  缺 `run_connection` 方法与 `listen_tcp` 形参。
- **修复**：仅改测试 fake——`_FakeServer.start` 改为 `(*args, **kwargs)`，
  并补 `run_connection` 空实现。生产代码不动，桌面 E2A 通道逻辑与远端测试意图
  （agent_teams 目录不被清理）均保留。修复后该用例通过。

## 五、验证

### 单测对比方法（关键）

1. 合并树全量 `pytest tests/unit_tests`：初跑 78 failed / 4533 passed。
2. 其中 18 个失败（test_expert_agent_group 11 + test_expert_team_api 7）是
   **venv 依赖过旧**——远端专家团特性要求 uv.lock 新钉的 openjiuwen
   （git 版 0.1.16@5297c25），`uv sync --extra dev` 后消失。
3. 为区分「远端自带 Windows 环境失败」与「合并引入回归」，用
   `git worktree` 检出纯远端 `origin/xiaoyi_0.2.4.beta3` 基线，对同一批失败文件重跑：
   - 纯远端基线：**59 failed**
   - 合并树：**60 failed**
   - 差集 = 仅 `test_run_does_not_delete_agent_teams_directory` 1 个（已按第四节修复）
4. 修复后合并树失败集 = 远端基线失败集（59 个），**零合并回归**。

### 59 个远端自带失败（Windows 环境，与本次合并无关，未改动）

| 分组 | 数量 | 失败性质 |
|---|---|---|
| server/hooks（test_executor 16 + test_user_hook_rail 10） | 26 | hooks 经 bash 执行 shell 命令，Windows 下 hook 未真正跑起来（`assert 'non_blocking_error' == 'success'` 等） |
| agentserver/test_deep_adapter_interrupt.py | 6 | 远端自身代码/测试不一致（`JiuWenSwarmDeepAdapter` 无 `_session_adapter_last_used`，纯远端同样失败） |
| test_web_file_download.py | 4 | Range 请求测试 stub 缺 `connection` 属性（远端自带） |
| agentserver/test_system_prompt_restructure.py | 4 | 临时目录缺 config.yaml fixture（远端自带） |
| extensions/test_agentos_router.py + cli/test_chat.py | 6 | 断言硬编码 POSIX 路径（`/mnt/...`、`/home/...`），Windows 下得到 `D:\...` |
| agentserver/test_evolve_command_edges.py | 3 | 测试桩 lambda 不收 `model_name_override` 关键字（远端自带） |
| gateway/test_session_restore_files_payload.py | 2 | `KeyError: 'restore_errors'`（远端自带） |
| 其余 8 个零散用例 | 8 | gbk 解码、symlink 权限、POSIX 路径、远端测试桩不匹配等 |

> 结论：这 59 个在纯远端 checkout 上同样失败，远端 CI 应跑在 Linux。
> 后续若要让 Windows 本地全绿，需要逐组修（ hooks 走 bash 探测/skip、
> 路径断言平台归一化等），超出本次合并范围。

## 六、注意事项

- 合并后 `uv.lock` 为双方并集，已执行 `uv sync --extra dev` 使 .venv 与之一致
  （openjiuwen 切到 git 钉版 0.1.16@5297c25）。**之后在本仓跑测试/构建前务必先 uv sync**。
- 远端 `interface.py` 大重写主要是结构性重构；本地唯一的 10 行增量已重放，
  如后续远端继续改该区域，rebase/merge 时留意 `handle_skills_toggle` 分支。
- 本记录文件不随框架代码进下游平移（放在仓库根，桌面仓平移时可按需拷贝）。
