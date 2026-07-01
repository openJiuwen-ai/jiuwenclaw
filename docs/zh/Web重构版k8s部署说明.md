# Web 重构版 k8s 部署说明（统一前端 + 认证中心 + 资源服务器 + 同源聊天）

在 Kubernetes 上部署 Web 重构版:登录 / 管理面(bot 与 IAM)/ 用户面(选 bot + 聊天 + 文件上传)。

> 关联文档:[Web 服务重构设计](./Web服务重构设计.md) · [Web 重构版本地部署说明](./Web重构版本地部署说明.md)

> 前提:Kubernetes 集群、`kubectl`、`docker`(节点容器运行时为 docker)、可访问镜像仓库、`yq` / `jq`;**多节点**集群还需节点间免密 ssh(用于分发本地构建的镜像)。

---

## 一、关于镜像:为何多一步"构建"

`deploy.sh` **只引用镜像、从仓库拉取,不构建**。其中 **4 个镜像为本次重构新增**,默认镜像仓库尚未发布,需先本地构建:

| 镜像 | 内容 | base |
|---|---|---|
| `identity` | 认证中心(OAuth2 + RS256 JWT) | manager 基础镜像 |
| `manager`(资源服务器 tag) | bot/IAM + JWT 验签 | manager 基础镜像 |
| `web`(broker + /file-api tag) | 聊天 WS + 文件 | web 基础镜像 |
| `webui`(统一前端 tag) | 外壳 + 同源 `/chat` 聊天 | node / nginx |

> 一旦这 4 个镜像随版本发布到镜像仓库,本步骤即可省略,部署退化为纯 `deploy.sh`(与其它服务一致)。`build-custom-images.sh` 负责构建,并在多节点集群下分发到各节点。

---

## 二、配置

复制示例配置后按需填写(镜像仓库地址与 4 个镜像 tag、模型 `MODEL_*`、数据库 `DB_TYPE` 等)。**仓库不提供含密钥的 `.env.custom`**:

```bash
cp deploy/.env.example deploy/.env.custom
# 编辑 deploy/.env.custom:*_IMAGE、MODEL_PROVIDER/MODEL_NAME/API_BASE/API_KEY、DB_TYPE 等
```

## 三、生成网关配置模板(固有预备步骤)

```bash
bash deploy/update_conf.sh
```

## 四、构建并分发自定义镜像

```bash
# 单节点集群:直接构建
bash docker/build-custom-images.sh

# 多节点集群:指定 worker 节点,脚本会 docker save/scp/load 分发镜像
WORKER=root@<worker-ip> bash docker/build-custom-images.sh
```

## 五、部署(显式列出模块)

不带模块名时 `deploy.sh` 默认只起 gateway,故需显式列出:

```bash
NS=test     # 目标命名空间,按需替换
cd deploy && ./deploy.sh up gateway web manager webui -n "$NS"
```

- `webui` 模块会**同时拉起认证中心 identity + 统一前端 webui 两个 pod**(合为一个部署模块,故模块列表里不单列 `identity`)。
- 基础设施(nfs / mysql / redis / minio)作为依赖自动拉起,固定部署在 `default` 命名空间。
- 对外仅 **webui 一个 NodePort**,部署日志末尾打印 `WEBUI_NODE_PORT: <端口>` 与 `IDENTITY deployed (ClusterIP)`。

## 六、验证

浏览器打开 `http://<任一节点IP>:<WEBUI_NODE_PORT>`:

1. `/auth` 登录:`admin / admin` → 管理面;`user1 / user1` → 用户面。
2. 管理面:新建 bot。
3. 用户面:选 bot → 聊天界面可加载并对话;文件上传成功(写入 MinIO)。

## 七、清理 / 重来

```bash
NS=test
kubectl delete ns "$NS"        # 仅清应用层;基础设施在 default 命名空间,不受影响
```

如需**强制重建镜像**:在各节点用 `docker rmi` 删除 `.env.custom` 中 4 个 `*_IMAGE` 对应的镜像,再重跑「步骤四」。

## 八、排查

- pod `ImagePullBackOff`:本地构建的镜像未分发到该 pod 所在节点 → 重跑「步骤四」(多节点确认 `WORKER`)。
- 新建 bot 报 500 / 验签失败:确认 identity pod 就绪;manager 的 `IDENTITY_PUBLIC_KEY_URL`(部署模板已带)。
- 上传报 400:确认 web 用的是含 `/file-api` 的镜像;MinIO 相关 env(部署模板已带)是否注入。
- configmap `already exists`:上一轮未清干净 → 删除命名空间后重来。
