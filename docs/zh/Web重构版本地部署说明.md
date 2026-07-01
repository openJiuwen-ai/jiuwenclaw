# Web 重构版本地部署说明

本说明针对**本机(非 k8s)**把 Web 重构版跑起来:认证中心 / 管理面(资源服务器)/ 聊天 broker / 统一前端,以普通进程启动。默认值基本为本地友好——数据库走 sqlite、验签地址默认指向本机,**登录 / 管理面 / 选 bot / 聊天界面**开箱即通。

> 关联文档:[Web 服务重构设计](./Web服务重构设计.md) · [Web 重构版 k8s 部署说明](./Web重构版k8s部署说明.md)

---

## 一、前提

- Python 环境(已 `pip install -e .[all]` 安装本仓库)、Node 18+。
- **数据库**:默认 `DB_TYPE=sqlite`,无需安装 mysql/postgresql;各服务落本地 `.db` 文件。
- 可选:**MinIO**(仅文件上传需要)、**gateway + agentserver + 模型 key**(仅聊天真出结果需要)。

---

## 二、启动后端(三个进程)

```bash
# 1) 认证中心(:8770,sqlite 自带,首启自动播种 admin/admin、user1/user1)
jiuwenclaw-identity

# 2) 管理面 / 资源服务器(:8765 REST、:8766 WS,sqlite)
#    默认 IDENTITY_PUBLIC_KEY_URL=http://127.0.0.1:8770/v1/auth/public_key → 本地零配置即可验签
jiuwenclaw-manager

# 3) 企业版 broker(:19000,聊天 WS + /file-api 文件)
jiuwenclaw-start web-enterprise
```

---

## 三、启动前端

统一外壳前端的 `vite.config.ts` 已内置反代(`/api`→8765、`/idp`→8770、`/ws`·`/file-api`→19000):

```bash
cd packages/jiuwenclaw-ee/claw_manager/web
npm install
npm run dev          # 默认 http://localhost:5173
```

**聊天 iframe**(`/chat` 同源是 webui nginx 才有的,本机 vite dev 没有),二选一:

- **方式 A(纯前端 dev)**:再起企业版聊天前端,并让外壳指向它
  ```bash
  cd jiuwenclaw/web_enterprise && npm install && npm run dev   # 如 http://localhost:5174
  # 启动外壳前设置(退回点对点 iframe,不经 /chat):
  VITE_CHAT_BASE_URL=http://localhost:5174 npm run dev   # 在 claw_manager/web 下
  ```
- **方式 B(最接近 k8s)**:用容器跑统一前端(含 `/chat`),后端指向宿主机
  ```bash
  docker compose -f docker/docker-compose.webui.yml up
  # webui 容器经 host.docker.internal 反代到本机 8765/8770/19000
  ```

---

## 四、访问与验证

浏览器打开前端入口(vite 的 `http://localhost:5173`,或 compose 暴露的端口):

1. 跳 `/auth`,`admin/admin` → 管理面;`user1/user1` → 用户面。
2. 管理面:新建 bot。
3. 用户面:选组织 → 选 bot → 聊天界面加载(`user_id/group_id/bot_id` 在聊天设置里只读)。

---

## 五、可选能力

| 想验 | 需补 | 配置 |
|---|---|---|
| 文件上传 | 本机 MinIO | `JIUWENCLAW_MINIO_ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET/SECURE/REGION` |
| 聊天真回复 | gateway + agentserver + 模型 | 运行时底座 + `MODEL_PROVIDER/MODEL_NAME/API_BASE/API_KEY` |

> 不补这两项时,**登录 / 管理面 / 组织与 bot / 聊天界面外壳**均可正常使用,仅"上传""聊天真出结果"不可用。

---

## 六、切换持久数据库(可选)

本地默认 sqlite。如需与 k8s 一致用 mysql/postgresql,只改环境变量(三个服务共用 `DB_TYPE`):

```bash
export DB_TYPE=mysql               # 或 postgresql
export DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=root DB_PASSWORD=...
# 各库名独立:identity / manager / gateway(mysql/pg 自动建库)
```
