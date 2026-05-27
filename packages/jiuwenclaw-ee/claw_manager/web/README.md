# JiuwenClaw Manager Web

Claw Manager 管理面 Web 前端。技术栈与 `jiuwenclaw/web` 完全对齐：

- React 18 + TypeScript + Vite
- Tailwind CSS（基于 CSS 变量的 JiuwenClaw 设计系统，dark/light 双主题）
- Zustand（轻量状态管理）
- react-i18next（zh / en）
- 极简自研 hash 路由（避免引入 react-router 额外体量，保持与已有项目一致的零依赖风格）

## 功能

1. **总览**：Manager / Manager-WS 健康状态、实例数、在线服务数（来自 `/api/health`、`/api/manager-ws/status`、`/api/v1/instances`）。
2. **服务组网**：分组卡片展示每个组网实例的 Gateway 在线状态，经 Manager WebSocket 心跳刷新（`/api/v1/instances`）。
3. **模型模板**：`/api/v1/model-templates` 的全量 CRUD（含 model_type、tags、参数 JSON 编辑）。
4. **扩展配置模板**：`/api/v1/extension-config-templates` 的全量 CRUD（钩子配置 JSON 编辑）。
5. **实例策略**：进入某个实例后，对默认模板映射 / 全局 / 服务 / Agent 四级策略做 CRUD。

## 启动

```bash
npm install
npm run dev
```

Vite 默认监听 `5273`，把 `/api/*` 反向代理到 `http://127.0.0.1:8765`（Claw Manager 默认 REST 端口）。

可通过 `.env.local` 覆盖：

```
VITE_API_BASE=/api
```

## 构建

```bash
npm run build
```

生成的静态产物在 `dist/`，可直接由 Claw Manager 反向代理或 Nginx 托管。
