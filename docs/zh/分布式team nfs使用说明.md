# 分布式 Team NFS 使用说明

本文说明如何用 `scripts/nfs/` 下的脚本，在分布式 Team 场景中共享 Team 工作空间。

默认共享工作空间目录：

```text
/root/.jiuwenclaw/.agent_teams
```



## 前提

- leader 和 teammate 都是 Linux 节点。
- 节点之间内网互通，优先使用内网 IP。
- NFS / RPC 相关端口已在安全组或防火墙中放行。
- 挂载或切换 NFS 前，先停止 leader / teammate 上正在运行的 JiuwenSwarm Team 进程。

下文使用占位符：

```text
<leader-private-ip>    leader 节点内网 IP
<teammate-private-ip>  teammate 节点内网 IP
```

## 1. Leader 启动 NFS Server

在 leader 节点执行：

```bash
cd /path/to/jiuwenclaw
sudo bash scripts/nfs/setup_nfs_server.sh --client-ip <teammate-private-ip>
```

脚本默认导出：

```text
/root/.jiuwenclaw/.agent_teams
```

如需显式指定：

```bash
sudo bash scripts/nfs/setup_nfs_server.sh \
  --client-ip <teammate-private-ip> \
  --export-dir /root/.jiuwenclaw/.agent_teams \
  --mount-point /root/.jiuwenclaw/.agent_teams \
  --fsid 1002
```

`--fsid` 用于避免部分系统执行 `exportfs` 时出现 `requires fsid=`。

## 2. Teammate 挂载 NFS Client

在 teammate 节点执行：

```bash
cd /path/to/jiuwenclaw
sudo bash scripts/nfs/setup_nfs_client.sh --server-ip <leader-private-ip>
```

如需显式指定：

```bash
sudo bash scripts/nfs/setup_nfs_client.sh \
  --server-ip <leader-private-ip> \
  --export-dir /root/.jiuwenclaw/.agent_teams \
  --mount-point /root/.jiuwenclaw/.agent_teams
```

client 脚本会在挂载前备份已有本地目录，备份目录形如：

```text
/root/.jiuwenclaw/.agent_teams.pre_nfs_backup_YYYYmmdd_HHMMSS
```

## 3. 检查挂载

在 leader 节点检查导出：

```bash
exportfs -v
showmount -e localhost
```

在 teammate 节点检查连通和挂载：

```bash
rpcinfo -p <leader-private-ip>
showmount -e <leader-private-ip>
mount | grep agent_teams
df -h | grep agent_teams
```

看到类似输出即表示挂载成功：

```text
<leader-private-ip>:/root/.jiuwenclaw/.agent_teams on /root/.jiuwenclaw/.agent_teams type nfs4
```

## 4. 双向读写验证

leader 写入：

```bash
echo from-leader > /root/.jiuwenclaw/.agent_teams/nfs_team_test.txt
```

teammate 读取并追加：

```bash
cat /root/.jiuwenclaw/.agent_teams/nfs_team_test.txt
echo from-teammate >> /root/.jiuwenclaw/.agent_teams/nfs_team_test.txt
```

leader 再读取：

```bash
cat /root/.jiuwenclaw/.agent_teams/nfs_team_test.txt
```

如果能看到两端写入的内容，说明共享生效。

## 5. 推荐启动顺序

1. 停止 leader / teammate 上的 JiuwenSwarm Team 进程。
2. leader 执行 `setup_nfs_server.sh`。
3. teammate 执行 `setup_nfs_client.sh`。
4. 确认 `mount | grep agent_teams` 正常。
5. 启动注册中心。
6. 启动 teammate 的 `app_agentserver`。
7. 启动 leader 后端和前端。
8. 新建会话重新执行分布式 Team 测试。

不要在 Team 运行过程中切换 `.agent_teams` 挂载关系。

## 6. 清理

teammate 卸载：

```bash
sudo umount /root/.jiuwenclaw/.agent_teams
```

如果提示 busy，先停止相关进程后重试。必要时使用：

```bash
sudo umount -l /root/.jiuwenclaw/.agent_teams
```

如需取消开机自动挂载，删除 `/etc/fstab` 中包含 `.agent_teams` 的 NFS 行。

leader 清理导出：

```bash
sudo rm -f /etc/exports.d/jiuwenclaw.exports
sudo exportfs -rav
```

## 7. 常见问题

- `exportfs` 提示 `requires fsid=`：使用 server 脚本默认的 `fsid=1002`，或显式传入 `--fsid 1002`。
- `rpcinfo` / `showmount` 超时：检查内网 IP、安全组、防火墙和 NFS 服务状态。
- Team 看不到共享文件：确认 NFS 已挂载后再启动 Team 进程，并使用新会话测试。
- 多个 teammate：每个 teammate 都执行 client 脚本；leader 侧需要允许对应 teammate 的内网 IP。
