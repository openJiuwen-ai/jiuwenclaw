# 安全审查指引（security）

> 默认**所有外部输入不可信**；鉴权、数据与依赖相关 diff 须加大扫描力度。

## 适用时机

- 触及鉴权、会话、支付、PII、上传、Webhook、管理端
- SQL/命令/模板/路径拼接、反序列化、依赖变更
- `security_review` 区块须填写；无相关变更标 `not_applicable` 并简述范围

## 三层边界（速记）

| 层级 | 要求 |
|------|------|
| **Always** | 边界校验、参数化查询、输出编码、HTTPS、强哈希密码、安全头、安全 Cookie、`audit` |
| **Ask First** | 新 auth 流、新敏感数据类别、新外部集成、CORS/上传/限流/提权变更 |
| **Never** | 密钥入库、日志敏感信息、仅客户端校验、禁用安全头、`eval`/不可信 `innerHTML`、localStorage 存会话 token、对用户暴露栈迹 |

## OWASP 速查（审查落点）

| 风险 | 查什么 | Must Fix 典型 |
|------|--------|----------------|
| 注入 | SQL/NoSQL/命令/模板/路径 | 用户输入拼进查询或 shell |
| 失效认证 | 会话、密码、重置 token | 明文密码、Cookie 无 httpOnly/secure |
| XSS | HTML/JSON 嵌入、绕过框架转义 | `innerHTML`/危险 `dangerouslySetInnerHTML` |
| 访问控制 | 每个受保护资源 | 只验登录不验归属；IDOR |
| 安全配置 | CSP/HSTS/CORS/默认账户 | 生产 `*` CORS、缺安全头 |
| 敏感数据 | 响应体、日志、缓存 | 返回 passwordHash/token；日志打全量 PII |
| 依赖 | lockfile、CVE、供应链脚本 | 可达的 critical/high 未处理 |

## 输入与输出

**边界校验**（路由/handler 入口）：

- 类型、长度、枚举、格式（email、日期）在**服务端**完成
- 拒绝时返回统一错误结构，不泄漏内部字段名/栈

**输出**：

- 使用框架默认转义；必须渲染 HTML 时 → 消毒库 + 明确理由
- API 响应做 **字段裁剪**（strip hash、resetToken 等）

## 鉴权与授权

```
authenticate? → authorize(resource/action)? → 审计敏感操作?
```

- **认证**：会话固定、过期、登出失效
- **授权**：默认拒绝；管理员操作二次校验角色
- **客户端不可信**：权限位、用户 ID 不得仅来自 query/body 而无服务端绑定

## 密钥与配置

- 密钥仅环境变量/密钥管理；`.env` 不入库
- 提交前：diff 中无 `password`/`secret`/`api_key`/`token` 明文
- 示例配置用 `.env.example` 占位

## 文件上传与 Webhook

- 白名单 MIME + 大小上限；关键场景校验 magic bytes
- Webhook：**验签** + 幂等 + 重放窗口
- 回调 URL 禁止 SSRF（内网元数据地址）

## 限流与滥用

- 登录/重置/注册：**更严**限流（独立于通用 API）
- 批量接口：分页上限、速率、超时

## 依赖审计 triage

```
critical/high
├── 生产可达调用路径? → 是：Must Fix（升级/替换/缓解）
└── 仅 dev 且不可达 → Should Fix + 跟踪日期
moderate/low → Should Fix 或 backlog，须写明理由
```

## 与 `review/result.json`

```json
"security_review": {
  "status": "PASS | FAIL | not_applicable",
  "items": [
    { "category": "secrets|input-validation|auth|logging|dependencies|sandbox",
      "status": "PASS|FAIL",
      "evidence": "检查了哪些文件/路径" }
  ]
}
```

- 任一 Must Fix 级安全问题 → `verdict: FAIL`，`security_review.status: FAIL`
- 无变更但模块属高危域 → 仍可抽查相邻未改文件是否引入回归（记入 scope）

## 快速清单

```markdown
- [ ] 外部输入均在边界校验；查询参数化
- [ ] 鉴权+授权覆盖所有受保护端点；无 IDOR
- [ ] 无密钥/令牌进入代码、日志、响应
- [ ] XSS/CSRF/Cookie/安全头符合项目基线
- [ ] 上传/Webhook/第三方回调有大小、类型、验签
- [ ] 依赖 audit：可达 critical/high 已处理或已记录缓解
- [ ] 错误响应不暴露内部实现
```

## 审查者自省

| 误区 | 纠正 |
|------|------|
| 「内网工具不用管」 | 内网仍是攻击面；横向移动常见 |
| 「框架自带安全」 | 工具需正确使用；配置错误仍 FAIL |
| 「以后加固」 | 安全债合并成本极高；能门控则门控 |

## 证据要求

PASS 须能列举**实际检查面**（如「核对了 `auth/middleware.ts` 与 3 个 PATCH 路由的 owner 校验」），禁止无证据 LGTM。
