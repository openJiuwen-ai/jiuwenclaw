# JiuwenClaw企业级部署工具
## 简介
JiuwenClaw 企业级部署工具是基于 Kubernetes 集群打造的一站式自动化部署套件，用于快速部署 JiuwenClaw 智能 AI 代理项目，整合 NFS、RabbitMQ、MySQL、PostgreSQL 等基础依赖，支持一键完成部署、卸载、重启等操作，提供灵活配置能力，降低企业级部署与运维成本。

## 入门

### 前置要求

安装jiuwenclaw前，请确保满足以下要求：

- 操作系统：Linux（推荐Unbuntu 20.04）
- 系统架构：amd64或arm64
- 硬件资源：至少2个计算节点，每个节点最低配置为16核CPU及32GB内存
- 运行环境：已预先搭建好Kubernetes集群，搭建流程可参考官方指导：https://kubernetes.io/zh-cn/docs/setup/

您可以通过以下命令检查：

```
# 操作系统
uname -s
# 系统架构
uname -m
# CPU核心数
nproc
# 检查内存大小 (GB)
free -g | grep Mem | awk '{print $2}'
# 集群节点状态
kubectl get nodes
```

配置 Master 节点至所有 Worker 节点的免密 SSH 登录，在 Master 节点执行以下命令：

```
ssh-copy-id <Worker节点IP>
```

### 部署jiuwenclaw

- 下载openjiuwen官网提供的企业级安装包：

```
https://openjiuwen-ci.obs.cn-north-4.myhuaweicloud.com/jiuwenclaw/JiuwenClaw/JiuwenClaw_deployTool_<VERSION>_<ARCH>.zip

```

- 解压缩：

```
unzip ***.zip
```

- 配置参数
请基于部署目录下 .env.example 配置模板，按需调整.env.custom文件中的环境变量、挂载路径、运行模式等核心参数，完成业务场景与部署环境的适配。一般情况下，如下几个参数是必改项:
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


- 一键部署

```
./deploy.sh up nfs          # 部署 NFS 存储模块（基础依赖，只需也只能一次）
./deploy.sh up rabbitmq     # 部署 RabbitMQ 存储模块（基础依赖，只需也只能一次）
./deploy.sh up mysql        # 部署 MySQL 存储模块（基础依赖，只需也只能一次）
./deploy.sh up postgresql   # 部署 PostgreSQL 存储模块（基础依赖，只需也只能一次）
./deploy.sh up              # 部署核心服务模块
./deploy.sh up web          # 部署 Web 前端模块（可选部署）
./deploy.sh up manager      # 部署 CLAW-Manager 管理模块（可选部署）
```

- 一键卸载

```
./deploy.sh down manager    # 卸载 CLAW-Manager 管理模块（按需卸载）
./deploy.sh down web        # 卸载 Web 前端模块（按需卸载）
./deploy.sh down            # 卸载核心服务模块
./deploy.sh down rabbitmq   # 卸载 RabbitMQ 存储模块（非必要不卸载）
./deploy.sh down mysql      # 卸载 MySQL 存储模块（非必要不卸载）
./deploy.sh down postgresql # 卸载 PostgreSQL 存储模块（非必要不卸载）
./deploy.sh down nfs        # 卸载 NFS 存储模块（非必要不卸载）
```
- 一键重启

```
./deploy.sh restart             # 重启核心服务模块（按需重启）
./deploy.sh restart web         # 重启 Web 前端模块（按需重启）
./deploy.sh restart manager     # 重启 CLAW-Manager 管理模块（按需重启）
./deploy.sh restart rabbitmq    # 重启 RabbitMQ 存储模块（按需重启）
./deploy.sh restart mysql       # 重启 MySQL 存储模块（按需重启）
./deploy.sh restart postgresql  # 重启 PostgreSQL 存储模块（按需重启）
./deploy.sh restart nfs         # 重启 NFS 存储模块（按需重启）
```

### 参数解析

命令格式
```
./deploy.sh [操作命令(必填)] [模块列表(选填)] [配置参数(选填)]
```

#### 操作命令（必填）

部署工具支持三种核心操作，用于管理服务生命周期：
- up：部署并启动指定的业务模块
- down：停止并卸载指定的业务模块
- restart：重启指定的业务模块

基础用法（无模块参数）：
```
./deploy.sh up       # 按默认规则部署核心模块
./deploy.sh down     # 按默认规则卸载核心模块
./deploy.sh restart  # 按默认规则重启核心模块
```
#### 模块列表（选填）

部署工具支持对以下四个独立模块进行精细化管理：
- `nfs`：NFS 存储服务模块（NFS 模块只能部署一次，且固定部署在 default 默认命名空间，自动忽略-n命名空间配置参数）
- `rabbitmq`：RabbitMQ 存储服务模块（RabbitMQ 模块只能部署一次，且固定部署在 default 默认命名空间，自动忽略-n命名空间配置参数）
- `mysql`：MySQL 存储服务模块（MySQL 模块只能部署一次，且固定部署在 default 默认命名空间，自动忽略-n命名空间配置参数）
- `postgresql`：PostgreSQL 存储服务模块（PostgreSQL 模块只能部署一次，且固定部署在 default 默认命名空间，自动忽略-n命名空间配置参数）
- `gateway`：Gateway 模块
- `web`：Web 前端页面服务模块
- `manager`：CLAW-Manager 管理模块

单模块操作示例：
```
./deploy.sh [操作命令] nfs          # 仅操作 NFS 模块
./deploy.sh [操作命令] rabbitmq     # 仅操作 RabbitMQ 模块
./deploy.sh [操作命令] mysql        # 仅操作 MySQL 模块
./deploy.sh [操作命令] postgresql   # 仅操作 PostgreSQL 模块
./deploy.sh [操作命令] gateway      # 仅操作 Gateway 模块
./deploy.sh [操作命令] web          # 仅操作 Web 模块
./deploy.sh [操作命令] manager      # 仅操作 CLAW-Manager 模块
```

当未指定模块参数时，部署工具默认操作 gateway 单模块。

重要约束
- `NFS 模块`：一个集群仅允许部署一个 NFS 模块，操作该模块必须显式指定模块参数方可
- `RabbitMQ 模块`：一个集群仅允许部署一个 RabbitMQ 模块，操作该模块必须显式指定模块参数方可
- `MySQL 模块`：一个集群仅允许部署一个 MySQL 模块，操作该模块必须显式指定模块参数方可
- `PostgreSQL 模块`：一个集群仅允许部署一个 PostgreSQL 模块，操作该模块必须显式指定模块参数方可
- `CLAW-Manager 模块`：操作该模块必须显式指定模块参数方可
- `Web 模块`：操作该模块必须显式指定模块参数方可
- `关联关系`：Web 模块与 Gateway 模块为一对一绑定关系，部署时必须使用相同命名空间（默认 default），否则服务无法互通


#### 配置参数（选填项）

- `-n`:  指定部署目标命名空间, 从而实现模块多实例隔离部署，不同命名空间的资源不冲突，默认值：default。需要注意的是：操作 NFS 模块时，该参数强制失效，固定部署于 default 命名空间。
- `--web-port`: 自定义Web模块对外访问端口，按需适配环境端口规划（范围：30000-32767）。若未传入该参数，且 .env.custom 文件中未配置 WEB_NODE_PORT 环境变量，程序将自动选取可用空闲端口。


参数使用示例：
```
./deploy.sh up -n test-ns                    # 部署核心模块至 test-ns 命名空间
./deploy.sh up web -n test-ns                # 部署Web模块至 test-ns 命名空间, 自动分配空闲端口
./deploy.sh up web -n test-ns --web-port 30080  # 部署Web模块至 test-ns 命名空间, 使用端口30080
./deploy.sh up nfs -n test-ns                # -n 参数无效，NFS 仍部署于 default空间
```