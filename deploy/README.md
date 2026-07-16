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

### 1.3 下载部署工具

在`部署节点`执行以下操作，下载并解压官方部署工具安装包，工具下载地址：

```
https://openjiuwen-ci.obs.cn-north-4.myhuaweicloud.com/JiuwenSwarm/JiuwenSwarm/JiuwenSwarm_deployTool_<VERSION>_<ARCH>.zip
```

解压命令：

```
unzip ***.zip
```

后续所有部署、运维命令，均需进入解压后的工具目录执行。

### 1.4 部署工具目录结构说明
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
├── log_handler.sh                        # 日志管理模块部署、运维脚本
├── configmap_secret_handler.sh           # 处理存放密码的ConfigMap的脚本
└── templates/                            # 所有 Kubernetes 资源模板配置目录
    ├── gateway-config-jiuwen.template.yaml # 网关业务配置模板
    ├── gateway.template.env                # 网关环境变量配置模板
    ├── gateway.template.yaml               # 网关 Kubernetes 部署资源模板
    ├── manager-server.template.yaml        # 管理服务后端部署模板
    ├── manager-web.template.yaml           # 管理前端部署模板
    ├── minio.template.yaml                 # MinIO 存储 Kubernetes 资源模板
    ├── mysql.template.yaml                 # MySQL 数据库 Kubernetes 资源模板
    ├── nfs.template.yaml                   # NFS 存储 Kubernetes 资源模板
    ├── nfs-sc.template.yaml                # NFS 存储供给 Kubernetes 资源模板
    ├── claw-pvc.template.yaml              # 业务内置持久卷PVC资源清单模板
    ├── postgresql.template.yaml            # PostgreSQL 数据库 Kubernetes 资源模板
    ├── redis.template.yaml                 # Redis 缓存 Kubernetes 资源模板
    ├── log.template.yaml                   # 日志模块 Kubernetes 资源模板
    ├── configmap-secret.template.yaml      # 专门存放密码的ConfigMap 资源模板
    └── web.template.yaml                   # Web 前端 Kubernetes 部署资源模板
```

### 1.5 修改配置文件`.env.custom`

修改配置前，请先查阅配置文件参数说明书 `.env.example`，明确各配置项含义与使用规则；再结合自身实际环境，按需调整配置文件`.env.custom`的参数，完成部署环境与业务场景适配。

以下为系统运行必需参数，需依据实际大模型服务凭证完整填写：
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

```

如需支持飞书客户端向机器人发起对话，则补充本段配置；如果仅需通过 Web 端与机器人交互则无需配置本段参数。
```
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
- **nfs-sc**：NFS 存储供给模块
- **mysql**：MySQL 存储服务模块
- **redis**：Redis 服务模块
- **postgresql**：PostgreSQL 存储服务模块
- **minio**：Minio 存储服务模块
- **log**：日志管理模块
- **gateway**：Gateway 模块
- **web**：Web 前端页面服务模块
- **manager**：CLAW-Manager 管理模块

**单模块操作示例：**
```
./deploy.sh [操作命令] nfs          # 仅操作 NFS 存储模块
./deploy.sh [操作命令] nfs-sc       # 仅操作 NFS 存储供给模块
./deploy.sh [操作命令] mysql        # 仅操作 MySQL 模块
./deploy.sh [操作命令] redis        # 仅操作 Redis 模块
./deploy.sh [操作命令] postgresql   # 仅操作 PostgreSQL 模块
./deploy.sh [操作命令] minio        # 仅操作 MinIO 模块
./deploy.sh [操作命令] log          # 仅操作日志管理模块
./deploy.sh [操作命令] gateway      # 仅操作 Gateway 模块
./deploy.sh [操作命令] web          # 仅操作 Web 模块
./deploy.sh [操作命令] manager      # 仅操作 Manager 模块
```

**重要约束：**

- **NFS / MySQL / Redis / PostgreSQL：** 以上基础依赖模块全局仅支持单次部署，固定运行于 default 命名空间，部署命令自动忽略自定义命名空间参数。
- **Web / Gateway / CLAW-Manager：** 业务服务模块需保持命名空间一致（为了环境隔离与日志运维，禁止使用default命令空间），否则服务间网络互通异常、功能不可用。

**使用示例：**

```
./deploy.sh up nfs        # 启动 NFS 存储模块（只需一次）
./deploy.sh up nfs-sc     # 启动 NFS 存储供给模块（只需一次）
./deploy.sh up mysql      # 启动 MySQL 存储模块（只需一次）
./deploy.sh up postgresql # 启动 PostgreSQL 存储模块（只需一次）
./deploy.sh up minio      # 启动 MinIO 存储模块（只需一次）
./deploy.sh up redis      # 启动 Redis 存储模块（只需一次）
./deploy.sh up log        # 启动日志管理服务模块（只需一次）
./deploy.sh up            # 启动 Gateway 服务模块
./deploy.sh up manager    # 启动 Manager 管理模块
./deploy.sh up web        # 启动 Web 前端模块

./deploy.sh down manager    # 卸载 Manager 管理模块（按需卸载）
./deploy.sh down web        # 卸载 Web 前端模块（按需卸载）
./deploy.sh down            # 卸载 Gateway 服务模块（按需卸载）
./deploy.sh down            # 卸载日志管理服务模块（非必要不卸载）
./deploy.sh down mysql      # 卸载 MySQL 存储模块（非必要不卸载）
./deploy.sh down redis      # 卸载 Redis 存储模块（非必要不卸载）
./deploy.sh down postgresql # 卸载 PostgreSQL 存储模块（非必要不卸载）
./deploy.sh down minio      # 卸载 MinIO 存储模块（非必要不卸载）
./deploy.sh down nfs        # 卸载 NFS 存储模块（非必要不卸载）
./deploy.sh down nfs-sc     # 卸载 NFS 存储供给模块（非必要不卸载）

./deploy.sh restart             # 重启 Gateway 服务模块（按需重启）
./deploy.sh restart web         # 重启 Web 前端模块（按需重启）
./deploy.sh restart manager     # 重启 Manager 管理模块（按需重启）
./deploy.sh restart log         # 重启日志管理模块（非必要不重启）
./deploy.sh restart mysql       # 重启 MySQL 存储模块（非必要不重启）
./deploy.sh restart redis       # 重启 Redis 存储模块（非必要不重启）
./deploy.sh restart postgresql  # 重启 PostgreSQL 存储模块（非必要不重启）
./deploy.sh restart minio       # 重启 MinIO 存储模块（非必要不重启）
./deploy.sh restart nfs         # 重启 NFS 存储模块（非必要不重启）
./deploy.sh restart nfs-sc      # 重启 NFS 存储供给模块（非必要不重启）
```

**重要说明：**
每当升级新版本服务时，对于**NFS、NFS-SC、MySQL、PostgreSQL、Redis、MinIO、Log** 等全局基础依赖组件应尽量保持不变，无需重复部署。仅需对业务服务**Gateway、Web、CLAW-Manager**进行版本替换：在旧版本部署目录中，依次卸载 业务模块；随后切换至新版本部署工具目录，启动对应新版业务模块。

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

#### 3.1.1 工具内置部署 NFS 服务（开发环境可用）

本部署工具提供一键部署能力，可直接在集群内快速搭建 NFS 存储服务：
```
./deploy.sh up nfs          # 部署 NFS 存储模块（基础依赖，只需也只能一次）
```

**限制条件**：
- 本部署工具仅支持 AMD64 架构 环境下单机部署；
- ARM64 架构、高可用 / 分布式 NFS 场景，需用户自行搭建外部 NFS 服务。

#### 3.1.2 工具内置部署 NFS 存储供给组件（开发环境可用）

本部署工具提供一键部署 NFS 存储供给组件，组件底层基于 nfs-subdir-external-provisioner 实现，提供标准 K8s StorageClass，业务 Pod 可通过 PVC 动态申领独立 NFS 存储子目录，实现数据持久化挂载。
```
./deploy.sh up nfs_sc          # 部署 NF S存储供给组件（基础依赖，只需也只能一次）
```
部署完成后自动生成对应 StorageClass，搭配 CLAW_MOUNT_TYPE=pvc 模式使用，可自动创建隔离式 NFS 持久卷。


#### 3.1.3 外部独立部署 NFS 服务（生产环境推荐）
生产环境下，推荐用户自行搭建高可用的外部 NFS 服务，搭建完成后需在自定义配置文件 `.env.custom` 中填写如下参数，完成外部 NFS 服务的对接工作：

```
# 设置产品组件的存储挂载模式为nfs
CLAW_MOUNT_TYPE="nfs"

# 外部 NFS 服务的连接地址
NFS_SERVER_ADDR=""

# 外部 NFS 服务的共享根目录路径
NFS_SHARE_PATH=""
```

#### 3.1.3 复用预创建 PVC 持久卷（生产环境推荐）

若客户有其他高可用企业级存储组件，并基于其组件的 StorageClass 预创建好了持久化 PVC 资源，业务组件可直接复用现有 PVC 完成存储挂载，配置如下：
```
# 设置产品组件的存储挂载模式为pvc
CLAW_MOUNT_TYPE="pvc"

# 外部 PVC 的名字，该 PVC 需存在，且与业务 Pod 处于同一 Namespace
CLAW_PVC=""

# 外部 PVC 绑定的 StorageClass 名称
NFS_SC_NAME=""
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

若需为该服务挂载外部 NFS 服务实现数据持久化，需在配置文件`.env.custom` 中设置：
```
# 外部 NFS 服务的连接地址
NFS_SERVER_ADDR=""

# 外部 NFS 服务的共享根目录路径
# 前置约束：共享目录下必须预先创建 mysql / postgresql 对应子目录用于数据存储
NFS_SHARE_PATH=""
```

**注意：** 本部署工具仅支持单实例数据库服务部署，如需高可用数据库服务，请采用外部独立部署方式。

#### 3.2.2 外部独立部署（生产环境推荐）

生产环境下，推荐用户自行搭建高可用的外部数据库服务，搭建完成后需在自定义配置文件 `.env.custom` 中填写如下参数，完成外部数据库服务的对接工作。

```
# 外部数据库服务的连接地址
DB_HOST=""

# 外部数据库服务服务的连接端口
DB_PORT=

# 外部数据库服务服务的用户名跟密码，账号权限区分规则：
# 1. 分库独立账号配置：分别定义 MANAGER_DB_USER / MANAGER_DB_PASSWORD、GATEWAY_DB_USER / GATEWAY_DB_PASSWORD，实现两个业务库账号、密码完全隔离
# 2. 全局统一账号配置：仅配置 DB_USER、DB_PASSWORD，Manager、Gateway 共用同一套数据库访问凭证
# 优先级规则：分库专属账号变量优先级 > 全局通用账号变量；若两类变量同时配置，以分库专属账号为准，全局账号配置自动失效
DB_USER=""
DB_PASSWORD=""
GATEWAY_DB_USER=""
GATEWAY_DB_PASSWORD=""
MANAGER_DB_USER=""
MANAGER_DB_PASSWORD=""

# (仅 PostgreSQL 有效) Manager 模块的专属 Schema 名称, 默认为public
MANAGER_PG_SCHEMA=""

# (仅 PostgreSQL 有效) Gateway 模块的专属 Schema 名称, 默认为public
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

系统支持两种对象存储接入方案：通过部署工具内置部署的 MinIO 实例，或对接外部独立对象存储服务，可按需选择。

#### 3.4.1 工具内置部署 MinIO 服务（开发环境可用）

本部署工具提供一键部署能力，可在集群内快速拉起单节点 MinIO 对象存储实例：
```
./deploy.sh up minio        # 部署 MinIO 存储模块（基础依赖，只需也只能一次）
```

若需为该服务挂载外部 NFS 服务实现数据持久化，需在配置文件`.env.custom` 中设置：
```
# 外部 NFS 服务的连接地址
NFS_SERVER_ADDR=""

# 外部 NFS 服务的共享根目录路径
# 前置约束：共享目录下必须预先创建 minio 对应子目录用于数据存储
NFS_SHARE_PATH=""
```
**注意：** 本部署工具仅支持单实例 MinIO 服务部署，如需高可用 MinIO 服务，请采用外部独立部署方式。

#### 3.4.2 使用外部 OBS 服务（生产环境推荐）

生产环境下，推荐用户自行搭建或购买高可用的外部 OBS 服务，搭建完成后需在自定义配置文件 `.env.custom` 中填写如下参数，完成外部 OBS 服务的对接工作。
```
# 外部对象存储(S3/MinIO兼容)接入地址
# 为空时，自动使用本工具内置部署的MinIO实例
OBS_URL=""

# 业务存储桶名称
# 使用脚本内置部署的 MinIO 时无需配置；对接外部OBS/MinIO服务必须填写
OBS_BUCKET=""

# 对象存储公网预览访问域名
# 仅文件需要浏览器公网直读场景配置，内网环境可不填
OBS_PUBLIC_BASE_URL=""

# 对象存储访问的 AccessKey
# 本工具内置部署的 MinIO：对应 MinIO 的用户账号；外部 OBS：填写云厂商/兼容S3服务的AccessKey
OBS_ACCESS_KEY=""

# 对象存储访问的 SecretKey
# 本工具内置部署的 MinIO：对应 MinIO 的用户密码；外部 OBS：填写云厂商/兼容S3服务的SecretKey
OBS_SECRET_KEY=""

# 客户端连接是否启用HTTPS
# 本工具内置部署的 MinIO：无需配置；外部 OBS：根据服务端SSL开关选择 true/false
OBS_SECURE="false"

# 对象存储区域标识
# 本工具内置部署的 MinIO：无需配置；外部 OBS：按需填写
OBS_REGION=""
```

### 3.5 部署日志管理服务（可选部署）
为便于统一采集、查看与治理业务日志，本部署工具提供可自选部署的日志管理模块，基于 Fluent Bit + Vector 架构实现全链路日志采集与处理能力。

**架构分工说明：** 
- **Fluent Bit**： 以节点级组件部署于所有 K8s 集群节点，负责抓取业务容器原始日志，并将日志数据统一转发至 Vector 服务；
- **Vector**： 承接日志后续处理工作，完成日志数据脱敏清洗后，将合规日志持久化存储至部署工具运行节点，存储路径由环境变量 CLAW_LOG_DIR 统一指定。

#### 3.5.1 前置配置
部署日志管理服务前，需在自定义配置文件`.env.custom`中完成如下核心参数配置：
```
# 宿主机日志持久化根目录
# 默认路径：$HOME/claw_logs，生产环境建议手动显式配置，保证日志路径固定有读写权限
CLAW_LOG_DIR=""

# 部署工具运行所在K8s节点名称
# 脚本将尝试自动识别节点名，若识别失败，请手动填写值，取值参考 kubectl get nodes 输出第一列节点名称
CURRENT_NODE_NAME=
```

#### 3.5.2 日志脱敏与输出策略配置
系统默认策略：业务日志默认输出至容器本地文件，且默认启用业务应用内置日志脱敏能力，日志管理服务（Vector）的脱敏功能默认关闭。
为优化业务应用运行性能，可关闭业务侧内置脱敏能力与容器本地日志文件输出，统一交由日志管理服务实现日志脱敏、采集、存储全流程管控，需在`.env.custom` 中新增如下配置：

```
# 日志采集服务（Vector）脱敏功能开关：开启统一日志脱敏处理
COLLECT_LOG_MASK_ENABLED=true

# 业务应用内置日志脱敏功能开关：关闭业务侧原生脱敏，避免重复处理、提升性能
LOG_MASK_ENABLED=false

# 业务应用容器本地日志文件输出开关：关闭容器本地文件落盘，减少容器IO开销
LOG_TO_FILE_ENABLED=false
```

#### 3.5.3 服务部署命令
完成上述配置后，执行以下命令一键部署日志管理模块，该模块为可选组件，且仅支持单次部署：
```
./deploy.sh up log          # 部署日志模块（可选部署，也只能部署一次）
```

部署完成后，日志采集服务将自动筛选集群内名称前缀为 `jiuwenclaw` 的 Pod 进行日志采集。采集完成后，日志文件按照 `命名空间/Pod名称/容器名称-日期.log` 层级目录结构持久化存储，目录组织示例如下：

```
└── myns
    ├── jiuwenclaw-agentserver-3nwipvx8cq-y7cu6
    │   ├── jiuwenbox-2026-07-14.log
    │   └── jiuwenclaw-agentserver-2026-07-14.log
    ├── jiuwenclaw-agentserver-n7iqunzg6h-3l6kf
    │   ├── jiuwenbox-2026-07-14.log
    │   └── jiuwenclaw-agentserver-2026-07-14.log
    ├── jiuwenclaw-gateway-668cccc968-66pxt
    │   ├── gateway-2026-07-14.log
    │   └── gateway-2026-07-15.log
    ├── jiuwenclaw-manager-server-569fc57f7-pn4v6
    │   └── manager-2026-07-15.log
    └── jiuwenclaw-web-545f77c477-drfcf
        └── web-2026-07-14.log
```



## 4 部署JiuwenSwarm企业级服务

JiuwenSwarm 企业级服务完整支持基于 Kubernetes 命名空间的多实例隔离部署，可在同一集群内通过不同命名空间部署多套独立运行的业务实例，实现环境隔离、多实例并行使用。

同一业务实例下的所有组件（Gateway、Web、CLAW-Manager）只有部署在同一个命名空间内才能服务调用和互通。而基础依赖组件（NFS、MySQL、Redis、MinIO、PostgreSQL）为所有业务实例的公共组件，无需随业务实例重复部署。

**注意**： 业务组件请勿部署至 default 默认命名空间，

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

若需为业务服务挂载外部 NFS 服务实现数据持久化，需在配置文件`.env.custom` 中设置：
```
# 设置产品组件的存储挂载模式为nfs
CLAW_MOUNT_TYPE="nfs"

# 外部 NFS 服务的连接地址
NFS_SERVER_ADDR=""

# 外部 NFS 服务的共享根目录路径
# 前置约束：共享目录下必须预先创建 jiuwenclaw 对应子目录用于数据存储
NFS_SHARE_PATH=""
```

若需为业务服务挂载外部 PVC 服务实现数据持久化，需在配置文件`.env.custom` 中设置：
```
# 设置产品组件的存储挂载模式为pvc
CLAW_MOUNT_TYPE="pvc"

# 外部 PVC 的名字，该 PVC 需存在，且与业务 Pod 处于同一 Namespace
CLAW_PVC=""

# 外部 PVC 绑定的 StorageClass 名称
NFS_SC_NAME=""
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

### 5.3 查看容器业务日志

本节适用于 Pod 状态为 Running 时，排查业务运行报错、接口异常、服务启动失败等问题。

**方式一：通过 kubectl 拉取标准输出日志**
日志输出内容较多时，建议将日志重定向至本地文件检索关键字 Error 定位异常：

```
kubectl logs -f <pod-name> -n <namespace> 2>&1 | tee <file-name>
```

**方式二：登录节点读取容器原始日志文件**

默认配置下，容器标准输出日志软链接统一存放于节点 /var/log/containers 目录下，仅对当前节点正在运行的 Pod 生成链接，

```
# ls /var/log/containers
jiuwenclaw-agentserver-gcivg9a6xk-aumi6_chenhui_jiuwenbox-f0c50f60193e14524f25f6f949d8c5e8333421812c339cfbaf37fc07a8f30f0f.log
jiuwenclaw-agentserver-gcivg9a6xk-aumi6_chenhui_jiuwenclaw-agentserver-e5b5a0f61ffd5cf817bf382734d6a50b6af15c81d2ea387c8106548e40005258.log
jiuwenclaw-gateway-6498fbc8d-nfhcl_chenhui_gateway-38a2134e8932dc4107c4e9cb7ce28e65531eac9f928c802d3cc49840ad363096.log
jiuwenclaw-web-fd64b644f-p42hf_chenhui_web-aeb66347df8980dea7f4697fe7f6b37eb1529c755efe2fbc778731646f70e7da.log
```
**注意**

1. kubelet 可自定义日志根路径 podLogsDir 参数，变更容器日志存储根目录。可通过如下命令查询当前节点生效日志根目录：
```
cat /var/lib/kubelet/config.yaml | grep podLogsDir
```
- 若命令有输出值：代表集群已自定义日志存储根目录，不再使用默认路径 /var/log/pods。
- 若命令无任何输出：代表未做自定义配置，使用 kubelet 默认底层日志目录 /var/log/pods

2. /var/log/containers 仅保留当前节点正常运行 Pod 的日志软链接；Pod 删除、漂移、容器完全退出后，软链接会被 kubelet 自动回收消失；



### 5.4 进入容器内部检查
若 Pod 处于 Running 状态，也可进入容器检查是否正常：
```
kubectl exec -it <pod-name> -n <namespace> -c <container-name> bash
```

### 5.5 利用工具一键收集某命名空间下所有 Pod 的实时日志

本部署工具提供轻量批量日志采集脚本，自动对某命名空间下所有 Pod、所有容器开启后台实时日志监听，日志自动落地至带时间戳的本地目录，便于问题回溯与关键字检索：
```
./collect_pods_log.sh <namespace>
```
**日志文件目录结构示例**
```
pod_logs_20260707_163022/
├── jiuwenclaw-agentserver-28pmfxp0gi-d9k4s-container1.log
├── jiuwenclaw-agentserver-28pmfxp0gi-d9k4s-container2.log
├── jiuwenclaw-gateway-6498fbc8d-nfhcl-gateway.log
└── jiuwenclaw-web-fd64b644f-p42hf-web.log
```

**注意**
- 建议业务 Pod 统一部署在独立自定义命名空间，禁止使用 default 命名空间，便于环境隔离与日志运维。
- 脚本执行前会自动清理当前指定命名空间的历史 kubectl 日志监听进程，避免多进程重复采集、日志重叠问题，且不会影响其他命名空间及其他用户的监听进程。
- 所有容器日志监听任务均在后台持续运行，实时追加新日志；Pod 新建、重建或重启后需重新执行脚本，接续监听新 Pod 日志。
- 长时间采集会持续落盘日志，需定期清理历史日志目录，避免磁盘占用过高。


### 5.5 启动日志管理服务收集所有业务日志

详情请见["部署日志管理服务"](#35-部署日志管理服务可选部署)


# FAQ 

## 如何在线调试业务代码

    在开发调试环境中，如果需要对 Gateway、Manager、AgentServer、Web 业务组件进行代码在线修改以及调试配置，可通过配置变量控制 Pod 启动时挂载宿主机本地源码，Pod 不再使用镜像内置代码，直接加载本地修改后的源码，无需重新构建推送镜像，即可实时调试功能。

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

# 是否要给 Web 模块mount代码
# 注意：Web源代码代码需要npm run build之后，才能mount进容器，
IS_MOUNT_WEB_CODE="false"
```

