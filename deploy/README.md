# JiuwenSwarm 企业级部署工具使用手册

JiuwenSwarm 企业级部署工具是适配 Kubernetes 集群的一站式自动化部署运维工具，专注于 JiuwenSwarm 企业版服务的快速搭建与全生命周期运维管理，有效解决了传统部署模式流程繁琐、环境适配复杂、运维操作碎片化、多组件协同部署易出错、标准化程度低等行业痛点。

该工具深度适配企业级集群交付场景，内置 MinIO 对象存储、PostgreSQL/MySQL 数据库、Redis 缓存等全套基础依赖组件的一键标准化部署能力，同时支持灵活对接外置 OBS 存储、独立数据库与缓存服务，适配多样化企业存量架构。工具高度封装部署、卸载、重启等核心运维操作，无需用户手动编写复杂的 Kubernetes YAML 资源文件，大幅降低操作门槛。此外，工具支持基于命名空间的多实例隔离部署，可自定义服务端口与各类环境参数，兼顾部署规范性与场景灵活度，能够有效提升集群搭建效率、统一企业运维标准，全面适配大规模、高可用、多租户的生产级集群运行环境。

## 1. 环境准备

### 1.1 基础环境要求

**安装JiuwenSwarm前，请确保满足以下要求：**

- 操作系统：Linux（推荐 Unbuntu 20.04）
- CPU 架构：**集群内所有节点架构必须一致，统一为 AMD64 或统一为 ARM64，禁止混合架构部署**
- 硬件资源：至少2个计算节点，每个节点最低配置为16核CPU、32GB物理内存
- 运行环境：已预先搭建好 Kubernetes 集群，搭建流程可参考官方指导：https://kubernetes.io/zh-cn/docs/setup/


**执行以下命令逐一校验节点环境信息：**

```
# 查看操作系统
uname -s

# 查看 CPU 架构
uname -m

# 查看 CPU 核心数量
nproc

# 查看总内存大小（单位GB）
free -g | grep Mem | awk '{print $2}'

# 校验K8s集群节点就绪状态
kubectl get nodes
```

### 1.2 预装软件依赖

| 工具 | 版本要求 | 校验范围  |校验方法|核心用途|
|------|----------|----------|----------|----------|
| yq | mikefarah/yq v4+ Go 版本 | 仅部署节点 |运行`yq --version`检查其版本是否符合要求|解析、修改 YAML 配置文件|
| jq | 无强制版本限制 | 仅部署节点 |执行 `which jq` 命令，校验其是否已安装| 解析并筛选 JSON 数据，提取所需字段|
| mount.nfs | 无强制版本限制 | 集群内所有节点  |执行 `which mount.nfs` 命令，校验其是否已安装|NFS客户端挂载工具，用于 Kubernetes 集群 Pod NFS 存储挂载|

### 1.3 配置免密登录（可选）
`部署节点`需是 Kubernetes 集群中的 Master 节点，建议预先配置`部署节点`至所有其他节点的免密 SSH 登录，便于部署工具跨节点互通访问。在`部署节点`执行以下命令即可完成配置：

```
ssh-copy-id <Worker节点IP>
```

该配置为可选项，不配置不影响基础使用。

### 1.4 下载部署工具

在`部署节点`执行以下操作，下载并解压官方部署工具安装包，工具下载地址：

```
https://openjiuwen-ci.obs.cn-north-4.myhuaweicloud.com/JiuwenSwarm/JiuwenSwarm/JiuwenSwarm_deployTool_<VERSION>_<ARCH>.zip
```

解压命令：

```
unzip ***.zip
```

后续所有部署、运维命令，均需进入解压后的工具目录执行。

### 1.5 部署工具目录结构说明
部署工具解压后完整目录结构及各文件/目录用途说明如下，业务配置统一在配置文件`.env.custom` 中调整，其他文件非必要不修改，：

```
JiuwenClaw_deployTool_0.0.74k_arm64/
├── .env.example                          # 配置文件的参数说明书
├── .env.custom                           # 配置文件（需用户手动修改）
├── README.md                             # 部署工具本地说明文档
├── deploy.sh                             # 部署工具运行入口脚本
├── args_handler.sh                       # 命令行参数解析与校验脚本
├── check_handler.sh                      # 环境、依赖、集群状态校验脚本
├── cmd_handler.sh                        # 底层命令封装与执行脚本
├── common.sh                             # 公共工具函数库
├── envfile_handler.sh                    # 处理环境变量文件脚本
├── global_vars.sh                        # 全局常量、默认参数定义脚本
├── gateway_handler.sh                    # Gateway网关模块部署、运维处理脚本
├── k8s_handler.sh                        # Kubernetes 集群资源操作核心脚本
├── manager_handler.sh                    # Manager 管理模块运维脚本
├── minio_handler.sh                      # MinIO 对象存储模块部署运维脚本
├── mysql_handler.sh                      # MySQL 数据库模块部署运维脚本
├── ports_handler.sh                      # 端口分配、校验、冲突检测管理脚本
├── postgresql_handler.sh                 # PostgreSQL 数据库模块部署运维脚本
├── redis_handler.sh                      # Redis 缓存模块部署运维脚本
├── nfs_handler.sh                        # NFS 模块部署运维脚本
├── template_handler.sh                   # Kubernetes 模板文件渲染、配置生成脚本
├── update_conf.sh                        # 配置更新、重载处理脚本
├── update_docker_registry.py             # 镜像仓库地址批量更新工具
├── web_handler.sh                        # Web 前端模块部署、运维脚本
└── templates/                            # 所有 Kubernetes 资源模板配置目录
    ├── gateway-config-jiuwen.template.yaml # 网关业务配置模板
    ├── gateway.template.env                # 网关环境变量配置模板
    ├── gateway.template.yaml               # 网关 Kubernetes 部署资源模板
    ├── manager-server.template.yaml        # 管理服务后端部署模板
    ├── manager-web.template.yaml           # 管理前端部署模板
    ├── minio.template.yaml                 # MinIO 存储 Kubernetes 资源模板
    ├── mysql.template.yaml                 # MySQL 数据库 Kubernetes 资源模板
    ├── nfs.template.yaml                   # NFS 存储 Kubernetes 资源模板
    ├── postgresql.template.yaml            # PostgreSQL 数据库 Kubernetes 资源模板
    ├── redis.template.yaml                 # Redis 缓存 Kubernetes 资源模板
    └── web.template.yaml                   # Web 前端 Kubernetes 部署资源模板
```

### 1.6 修改配置文件`.env.custom`

修改配置前，请先查阅配置文件参数说明书 `.env.example`，明确各配置项含义与使用规则；再结合自身实际环境，按需调整配置文件`.env.custom`的参数，完成部署环境与业务场景适配。
以下参数为常规必改填配置，需按实际信息填写：

```
# ====================== 大模型接口配置 ======================
# 模型厂商标识（如：OpenAI等）
MODEL_PROVIDER=""

# 大模型名称
MODEL_NAME=""

# 大模型API基础地址
API_BASE=""

# 大模型鉴权密钥
API_KEY=""

# ==============================================================
# 飞书机器人配置（FEISHU_BOTS）
# 配置格式：一行一个机器人，规则为 Bot Name:App ID:App Secret
# 示例：
# FEISHU_BOTS="
# bot_name_1:app_id_1:app_secret_1
# bot_name_2:app_id_2:app_secret_2
# bot_name_3:app_id_3:app_secret_2
#"

FEISHU_BOTS="
"
```

## 2 部署工具命令行介绍

**命令格式:**
```
./deploy.sh [操作命令(必填)] [模块列表(选填)] [配置参数(选填)]
```

### 2.1 操作命令（必填）

**部署工具支持三种核心操作，用于管理服务生命周期：**
- **up**：部署并启动指定的业务模块
- **down**：停止并卸载指定的业务模块
- **restart**：重启指定的业务模块

**基础用法**（当未指定模块参数时，部署工具默认操作 gateway 单模块）：
```
./deploy.sh up       # 部署 Gateway 模块
./deploy.sh down     # 卸载 Gateway 模块
./deploy.sh restart  # 重启 Gateway 模块
```

### 2.2 模块列表（选填）

**部署工具支持对以下独立模块进行精细化管理：**
- **nfs**：NFS 存储服务模块
- **mysql**：MySQL 存储服务模块
- **redis**：Redis 服务模块
- **postgresql**：PostgreSQL 存储服务模块
- **minio**：Minio 存储服务模块
- **gateway**：Gateway 模块
- **web**：Web 前端页面服务模块
- **manager**：CLAW-Manager 管理模块

**单模块操作示例：**
```
./deploy.sh [操作命令] nfs          # 仅操作 NFS 模块
./deploy.sh [操作命令] mysql        # 仅操作 MySQL 模块
./deploy.sh [操作命令] redis        # 仅操作 Redis 模块
./deploy.sh [操作命令] postgresql   # 仅操作 PostgreSQL 模块
./deploy.sh [操作命令] minio        # 仅操作 MinIO 模块
./deploy.sh [操作命令] gateway      # 仅操作 Gateway 模块
./deploy.sh [操作命令] web          # 仅操作 Web 模块
./deploy.sh [操作命令] manager      # 仅操作 Manager 模块
```

**重要约束：**

- **NFS / MySQL / Redis / PostgreSQL：** 以上基础依赖模块全局仅支持单次部署，固定运行于 default 命名空间，部署命令自动忽略自定义命名空间参数。
- **Web / Gateway / CLAW-Manager：** 业务服务模块需保持命名空间一致，否则服务间网络互通异常、功能不可用。


**使用示例：**

```
./deploy.sh up nfs        # 启动 NFS 存储模块（只需一次）
./deploy.sh up mysql      # 启动 MySQL 存储模块（只需一次）
./deploy.sh up postgresql # 启动 PostgreSQL 存储模块（只需一次）
./deploy.sh up minio      # 启动 MinIO 存储模块（只需一次）
./deploy.sh up redis      # 启动 Redis 存储模块（只需一次）
./deploy.sh up            # 启动 Gateway 服务模块
./deploy.sh up manager    # 启动 Manager 管理模块
./deploy.sh up web        # 启动 Web 前端模块

./deploy.sh down manager    # 卸载 Manager 管理模块（按需卸载）
./deploy.sh down web        # 卸载 Web 前端模块（按需卸载）
./deploy.sh down            # 卸载 Gateway 服务模块（按需卸载）
./deploy.sh down mysql      # 卸载 MySQL 存储模块（非必要不卸载）
./deploy.sh down redis      # 卸载 Redis 存储模块（非必要不卸载）
./deploy.sh down postgresql # 卸载 PostgreSQL 存储模块（非必要不卸载）
./deploy.sh down minio      # 卸载 MinIO 存储模块（非必要不卸载）
./deploy.sh down nfs        # 卸载 NFS 存储模块（非必要不卸载）
./deploy.sh restart             # 重启 Gateway 服务模块（按需重启）
./deploy.sh restart web         # 重启 Web 前端模块（按需重启）
./deploy.sh restart manager     # 重启 Manager 管理模块（按需重启）
./deploy.sh restart mysql       # 重启 MySQL 存储模块（非必要不重启）
./deploy.sh restart redis       # 重启 Redis 存储模块（非必要不重启）
./deploy.sh restart postgresql  # 重启 PostgreSQL 存储模块（非必要不重启）
./deploy.sh restart minio       # 重启 MinIO 存储模块（非必要不重启）
./deploy.sh restart nfs         # 重启 NFS 存储模块（非必要不重启）
```

**重要说明：**
每当升级新版本服务时，对于**NFS、MySQL、PostgreSQL、Redis、MinIO** 等全局基础依赖组件应尽量保持不变，无需重复部署。仅需对业务服务**Gateway、Web、CLAW-Manager**进行版本替换：在旧版本部署目录中，依次卸载 业务模块；随后切换至新版本部署工具目录，启动对应新版业务模块。

### 2.3 配置参数（选填）

**参数说明：**
- `-n`:  指定部署目标命名空间, 从而实现模块多实例隔离部署，不同命名空间的资源不冲突，默认值：`default`。需要注意的是：操作基础依赖模块时，该参数强制失效，固定部署于 `default` 命名空间。
- `--web-port`: 自定义Web模块对外访问端口，按需适配环境端口规划（范围：30000-32767）。若未传入该参数，且 `.env.custom` 文件中未配置 WEB_NODE_PORT 环境变量，程序将自动选取可用空闲端口。
- `--manager-web-port`: 自定义 `Manager Web UI` 对外访问端口（范围：30000-32767）。若未传入该参数，且 `.env.custom` 文件中未配置 `MANAGER_WEB_NODE_PORT` 环境变量，程序将自动选取可用空闲端口。
- `--render-only`：只渲染模板输出文件至 conf 目录，不操作集群、不校验集群资源

**参数使用示例：**
```
./deploy.sh up -n test-ns                    # 部署核心模块至 test-ns 命名空间
./deploy.sh up web -n test-ns                # 部署 Web 模块至 test-ns 命名空间, 自动分配空闲端口
./deploy.sh up web -n test-ns --web-port 30080  # 部署 Web 模块至 test-ns 命名空间, 使用端口30080
./deploy.sh up nfs -n test-ns                # -n 参数无效，NFS 仍部署于 default 空间
```

## 3 部署基础依赖服务

所有基础依赖服务仅需全局部署一次。

### 3.1 部署 NFS 服务

#### 3.1.1 工具内置部署（开发环境可用）

本部署工具提供一键部署能力，可直接在集群内快速搭建 NFS 存储服务：
```
./deploy.sh up nfs          # 部署 NFS 存储模块（基础依赖，只需也只能一次）
```

**限制条件**：
- 本部署工具仅支持 AMD64 架构 环境下单机部署；
- ARM64 架构、高可用 / 分布式 NFS 场景，需用户自行搭建外部 NFS 服务。

#### 3.1.2 外部独立部署（生产环境推荐）
生产环境下，推荐用户自行搭建高可用的外部 NFS 服务，搭建完成后需在自定义配置文件 `.env.custom` 中填写如下参数，完成外部 NFS 服务的对接工作：

```
# 外部 NFS 服务的连接地址
NFS_SERVER_ADDR=""

# 本工具内置部署的 AgentServer 组件在外部 NFS 服务中的共享目录，请确保该目录存在，且目录属主UID/GID统一为1000
JIUWENCLAW_NFS_PATH=""

# 本工具内置部署的 MySQL 服务在 外部 NFS 服务中的共享目录（外部 MySQL 服务不用填），请确保该目录存在
MYSQL_NFS_PATH=""

# 本工具内置部署的 PostgreSQL 服务在 外部 NFS 服务中的共享目录（外部 PostgreSQL 服务不用填），请确保该目录存在
POSTGRES_NFS_PATH=""

# 本工具内置部署的 MinIO 服务在 外部 NFS 服务中的共享目录（未使用 "本工具内置部署的 MinIO 服务" 作为OBS服务的不用填），请确保该目录存在
MINIO_NFS_PATH=""
```

### 3.2 部署数据库服务

本产品支持 SQLite、PostgreSQL、MySQL 三种数据库类型，可根据具体需求三选一配置即可。选定数据库类型后，在配置文件 `.env.custom` 中指定：
```
# 数据库类型，支持mysql、sqlite、postgresql, 默认为sqlite
DB_TYPE="sqlite"
```

**说明：** SQLite 为嵌入式内置数据库，无需部署。

#### 3.2.1 工具内置部署（开发环境可用）

本部署工具提供一键部署能力，可在集群内快速拉起 MySQL 或 PostgreSQL 单实例服务：
```
./deploy.sh up mysql        # 部署 MySQL 存储模块（基础依赖，只需也只能一次）
./deploy.sh up postgresql   # 部署 PostgreSQL 存储模块（基础依赖，只需也只能一次）
```
**注意：** 本部署工具仅支持单实例数据库服务部署，如需高可用数据库服务，请采用外部独立部署方式。

#### 3.2.2 外部独立部署（生产环境推荐）

生产环境下，推荐用户自行搭建高可用的外部数据库服务，搭建完成后需在自定义配置文件 `.env.custom` 中填写如下参数，完成外部数据库服务的对接工作。

**MySQL 外部服务的配置：**
```
# 外部 MySQL 服务的连接地址
MYSQL_HOST=""

# 外部 MySQL 服务的连接端口
MYSQL_PORT=""

# 外部 MySQL root的密码
MYSQL_ROOT_PASSWORD=""
```

**PostgreSQL 外部服务的配置：**
```
# 外部 PostgreSQL 服务的连接地址
POSTGRES_HOST=""

# 外部 PostgreSQL 服务的连接端口
POSTGRES_PORT=

# 外部 PostgreSQL 服务的密码
POSTGRES_PASSWORD=""

# Manager 模块 PostgreSQL 专属 Schema 名称, 默认为public
MANAGER_PG_SCHEMA=""

# Gateway 模块 PostgreSQL 专属 Schema 名称, 默认为public
GATEWAY_PG_SCHEMA=""
```

### 3.3 部署redis服务

当Gateway开启主备模式时，需要提前部署Redis服务。

#### 3.3.1 工具内置部署（开发环境可用）

本部署工具提供一键部署能力，可在集群内快速拉起单节点 Redis 实例：
```
./deploy.sh up redis        # 部署 Redis（只需也只能部署一次）
```

**注意：** 本部署工具仅支持单实例Redis服务部署，如需高可用Redis服务，请采用外部独立部署方式。

#### 3.3.2 外部独立部署（生产环境推荐）

生产环境下，推荐用户自行搭建高可用的外部 Redis 服务，搭建完成后需在自定义配置文件 `.env.custom` 中填写如下参数，完成外部 Redis 服务的对接工作。

```
# 外部 Redis 服务的连接地址
REDIS_HOST=""

# 外部 Redis 服务的连接端口
REDIS_PORT=

# 外部 Redis 服务的密码
REDIS_PASSWORD=""

```

### 3.4 部署对象存储（OBS）服务

系统支持两种对象存储接入方案：通过部署工具内置部署的 MinIO 实例，或对接外部独立对象存储服务，可按需选择。选定方案后，在 `.env.custom` 中配置存储类型：

```
# OBS 存储服务类型，目前支持minio
OBS_TYPE=""
```

#### 3.4.1 工具内置部署（开发环境可用）

本部署工具提供一键部署能力，可在集群内快速拉起单节点 MinIO 对象存储实例：
```
./deploy.sh up minio        # 部署 MinIO 存储模块（基础依赖，只需也只能一次）
```

#### 3.4.2 外部独立部署（生产环境推荐）

生产环境下，推荐用户自行搭建高可用的外部 MinIO 服务，搭建完成后需在自定义配置文件 `.env.custom` 中填写如下参数，完成外部 MinIO 服务的对接工作。
```
# 外部 MinIO 服务的连接地址（格式：host:port）
MINIO_URL=""

# 外部 MinIO 服务的 Root 用户名
MINIO_ROOT_USER=""

# 外部 MinIO 服务的 Root 用户的密码
MINIO_ROOT_PASSWORD=""

# 外部 MinIO 服务是否启用HTTPS加密连接
MINIO_SECURE="false"
```

#### 3.4.3 使用外部 OBS 服务（待开放）


## 4 部署JiuwenSwarm企业级服务

JiuwenSwarm 企业级服务完整支持基于 Kubernetes 命名空间的多实例隔离部署，可在同一集群内通过不同命名空间部署多套独立运行的业务实例，实现环境隔离、多实例并行使用。

同一业务实例下的所有组件（Gateway、Web、CLAW-Manager）只有部署在同一个命名空间内才能服务调用和互通。而基础依赖组件（NFS、MySQL、Redis、MinIO、PostgreSQL）为所有业务实例的公共组件，无需随业务实例重复部署。

### 4.1 部署 Gateway (必选部署)

Gateway 是 JiuwenSwarm 的多渠道接入网关与消息调度核心，负责 AgentServer 生命周期管控、多平台渠道接入、消息双向路由转发、会话关系映射等关键能力。作为客户端与 AgentServer 之间的中转枢纽，该组件为系统强制部署项，缺失 Gateway 将导致整体业务系统无法正常运行。执行以下命令完成网关部署：

```
./deploy.sh up              # 部署 Gateway 核心网关模块
```

注意：默认以单机单实例模式运行；若需启用双实例主备高可用架构，请在启动前，修改配置文件 `.env.custom` 如下参数：

```
# Gateway 部署模式：standalone（默认，不连 Redis）| active-standby（双实例主备，需 Redis）
DEPLOYMENT_MODE=standalone
```
### 4.2 部署 Manager（可选部署）

Manager 为平台管理模块，提供策略下发和配置、业务实例监控等管理能力，用于辅助运维人员完成系统管控。该组件为可选部署项，可根据实际运维需求选择性部署。执行以下命令完成管理模块部署：

```
./deploy.sh up manager      # 部署 Manager 管理模块
```

注意，这会启动 `jiuwenclaw-manager-server` 后端服务跟 `jiuwenclaw-manager-web` 前端组件，如果不需要 `jiuwenclaw-manager-web` 前端组件的，请在启动前，修改配置文件 `.env.custom` 如下参数：
```
# 控制是否启动 Manager 前端模块
IS_UP_MANAGER_WEB=false
```

### 4.3 部署 Web（可选部署）

Web 为 JiuwenSwarm 企业版面向终端用户的对话可视化前端，用于用户直接和大模型机器人在线对话功能。该模块为可选组件，若业务仅通过飞书渠道与机器人交互、无需网页端对话入口，则可不部署。

```
./deploy.sh up web          # 部署 Web 前端模块（可选部署）

```

## 5 服务异常排查

系统服务运行异常时，可按以下步骤逐层定位故障根因：

### 5.1 查看 Pod 状态
查看集群全部 Pod，识别 Pending、CrashLoopBackOff、ImagePullBackOff 等异常运行状态：
```
kubectl get pods -A
```
### 5.2 查看 Pod 事件（快速定位启动故障）

适用于排查 Pod 处于 Pending 等无法正常启动场景，可定位镜像拉取失败、资源配额不足、调度异常、健康探针校验失败等问题：
```
kubectl describe pod <pod-name> -n <namespace>
```

若需查询节点 kubelet 底层详细报错，执行如下命令实时输出日志：

```
journalctl -u kubelet -f
```

### 5.3 查看容器业务日志（业务报错、启动异常）
适用于排查 Pod 处于 Running 状态的业务端报错的场景， 日志输出内容较多时，建议将日志重定向至本地文件检索关键字 Error 定位异常：

```
kubectl logs -f <pod-name> -n <namespace> 2>&1 | tee <file-name>
```

### 5.4 进入容器内部检查
若 Pod 处于 Running 状态，也可进入容器检查是否正常：
```
kubectl exec -it <pod-name> -n <namespace> -c <container-name> bash
```

# FAQ

## 如何在线调试业务代码

    在开发调试环境中，如果需要对 Gateway、Manager、AgentServer 业务组件进行代码在线修改以及调试配置，可通过配置变量控制 Pod 启动时挂载宿主机本地源码，Pod 不再使用镜像内置代码，直接加载本地修改后的源码，无需重新构建推送镜像，即可实时调试功能。

**使用前注意事项：**
- 提前在部署节点的宿主机上拉取对应模块完整源码，并切换至指定开发分支
- 具备部署节点的宿主机上的本地源码目录可读权限；
- 不调试某个模块时，对应路径变量必须留空，否则会覆盖镜像内置代码。

**使用方法：**
    请在启动服务前，修改配置文件 `.env.custom` 如下参数：

```
# 设置运行模式为开发模式，开启本地源码挂载调试逻辑
MODE=dev

# ===================== jiuwenclaw 模块调试 =====================
# CLAW源码宿主机绝对路径，仅调试claw组件时填写；不调试直接留空
# 源码仓库：https://gitcode.com/openJiuwen/jiuwenswarm
# 代码分支：dev/enterprise_kub
CLAW_CODE_PATH=""

# ===================== agent-runtime 模块调试 =====================
# Runtime源码宿主机绝对路径，仅调试runtime组件时填写；不调试直接留空
# 源码仓库：https://gitcode.com/openJiuwen/agent-runtime
# 代码分支：develop
RUNTIME_CODE_PATH=""
```
