# JiuwenBox Python FastPath

JiuwenBox 沙箱执行 `python3 -c <code>` 类请求时，除默认每请求 spawn 一个新解释器的路径外，还内置一条 **Python ForkServer FastPath**（下称 FastPath）。FastPath 在每个沙箱守护进程内维护少量常驻解释器 worker，`fork()` 子进程执行用户代码，省去每请求冷启动开销。

> FastPath 是**默认关闭**的内部特性开关，不影响默认 `/exec` 行为，也不新增任何外部 API。

## 是什么

- 仅对 `python3 -c <code>` 这种简单命令形状生效；其他命令走原路径。
- worker 是沙箱守护进程的直接子进程，继承与守护进程完全相同的 bwrap 命名空间 / userns / cgroup / seccomp / Landlock / mount 隔离边界。**不放宽任何沙箱隔离不变量。**
- 每个 worker 单线程，逐请求 `fork()` 一个子进程跑用户代码。

## 如何开启 / 关闭

通过服务端进程环境变量控制（守护进程经 bwrap 继承，两侧一致）：

| 环境变量 | 说明 | 默认 |
| --- | --- | --- |
| `JIUWENBOX_PYTHON_FASTPATH` | `1` 开启，其他/未设 = 关闭 | 关闭 |
| `JIUWENBOX_PYTHON_FASTPATH_WORKERS` | 每沙箱常驻 worker 数（硬上限 4） | 2 |
| `JIUWENBOX_PYTHON_FASTPATH_IDLE_TIMEOUT` | 空闲多久后回收 worker 到 0（秒，下限 10） | 300 |
| `JIUWENBOX_PYTHON_FASTPATH_MAX_SANDBOXES` | 单服务端进程内允许激活 worker 池的沙箱全局上限（上限 1000） | 50 |

在 systemd 单元里设置示例：

```ini
[Service]
Environment=JIUWENBOX_PYTHON_FASTPATH=1
Environment=JIUWENBOX_PYTHON_FASTPATH_WORKERS=2
```

不设置任何 FastPath 参数时，FastPath 关闭，行为与未启用此特性的旧版本完全等价（可直接升级）。

## 默认值与资源影响

- **worker 懒启动**：沙箱首次收到 FastPath 请求才 spawn worker，空闲沙箱持有 0 个 worker。
- **机器级 worker 上界**：`MAX_SANDBOXES(默认 50) × per-sandbox worker(默认 2，硬上限 4)`，最坏情况 `50 × 4 = 200` 个常驻解释器进程。按部署规模调整 `MAX_SANDBOXES`。
- **空闲回收**：达到 `IDLE_TIMEOUT` 后 pool 回收到 0，释放常驻进程内存。
- **冷启动风暴**：`MAX_SANDBOXES` 同时限制同时激活的沙箱数，收敛多沙箱同时冷启动的 blast radius。

## 非法值处理 / 安全降级

- 所有 FastPath 环境变量：非法值或越界值统一降级到默认值，**不抛异常、不阻断 JiuwenBox 启动**。
- 配置错误不会导致服务无法启动；最坏情况为 FastPath 不生效，回退到默认 `/exec` 路径。

## fallback 行为

FastPath 在以下情况自动回退到默认 `subprocess.Popen` 路径，对调用方透明：

- 特性未开启 / 命令不是 `python3 -c` 形状；
- 沙箱已达 `MAX_SANDBOXES` 全局激活上限；
- worker spawn 失败、worker 不可用、响应超时；
- 熔断器开启（连续失败达阈值）。

熔断器（circuit breaker）：连续失败达阈值后开路，在冷却期内直接 fallback，冷却结束后放一次半开探测，成功则闭路。worker 被同沙箱用户 kill 后会自动补充恢复。

## 可观测性

- 统计快照（计数器）节流写入沙箱内 `/tmp/fastpath_stats.json`，仅供主机侧运维 / 性能脚本经正常 exec 读取，纯诊断，不参与控制流。

## 参考

详细性能数据与实验历史见仓库根 `docs/deploy-perf/`（不随包发布）。
