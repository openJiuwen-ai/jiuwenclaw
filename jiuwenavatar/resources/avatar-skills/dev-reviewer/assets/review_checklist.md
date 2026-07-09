# Code Review Checklist

## 五维总览

| 维度 | 侧重 | 指引 |
|------|------|------|
| **Code** | 架构与设计 | [code.md](../references/dimensions/code.md) |
| **Clean** | 代码编写 | [clean.md](../references/dimensions/clean.md) + [google_style_index.md](google_style_index.md) |
| **Spec** | doc/计划对齐 | [spec.md](../references/dimensions/spec.md) |
| **Security** | 安全 | [security.md](../references/dimensions/security.md) |
| **Performance** | 性能 | [performance.md](../references/dimensions/performance.md) |

先扫五维，再扫下方「通用」节。Code 与 Clean 勿重复勾选同一问题（命名风格 → Clean；分层错位 → Code）。

---

## Code — 架构与设计

> [code.md](../references/dimensions/code.md)

- [ ] 分层合理（handler / service / domain），业务未堆在入口层；与 `design.md` 边界一致
- [ ] 无循环依赖、反向依赖、不当跨层调用
- [ ] 主路径 + 边界 + 错误路径；并发/幂等/事务边界可解释
- [ ] 公开 API / 错误模型 / 数据模型清晰；无未说明的兼容性破坏
- [ ] 无复制粘贴双份真相；变更体量可审（~300 行内单一主题）
- [ ] 测试验证**行为**而非实现细节；关键路径有覆盖
- [ ] 日志/可观测性字段与项目约定一致

### 深度分析（Code 维度必须完成）

- [ ] **数据流追踪**：对每个被修改函数，追踪数据从入口到出口的完整路径；是否存在静默丢失（匹配失败、异常吞掉、分支未覆盖）？
- [ ] **中间状态一致性**：多步操作（写 A → 写 B → 写 C）中，某一步失败时前面的修改是否已持久化？有无事务/补偿/回滚？
- [ ] **边界条件全覆盖**：空值/零值/极值/非法枚举传入时行为是什么？外部依赖（LLM/API/DB/文件系统）失效时如何处理？
- [ ] **异常处理质量**：`try/except` 是否过于宽泛？异常是否吞掉而不记日志？异常后系统是否处于一致状态？
- [ ] **向下兼容**：修改现有行为是否影响旧调用方？API 字段/序列化/DB schema 变更有无迁移说明？新语义是否与函数名/文档注释一致？
- [ ] **系统性扫描**：功能涉及的所有环节（输入→处理→存储→输出）是否都考虑了失效模式？改了一个环节，上下游是否跟着改了？

---

## Clean — 代码编写

> [clean.md](../references/dimensions/clean.md)；语言规范见 [google_style_index.md](google_style_index.md)

- [ ] Python：导入顺序、无相对 import、命名符合 pyguide（`lower_with_under` / `CapWords`）
- [ ] Python：无 mutable 默认参数；公共 API 有 docstring 与合理类型注解
- [ ] TS/JS：命名/import 与邻文件一致；无 `any`（公共 API）、`eval`/`debugger`/`with` 等禁项
- [ ] Java：无 wildcard import；@Override；空 catch 有注释；public API 有 Javadoc
- [ ] 新代码与项目 formatter/linter 配置兼容；冲突已在 finding 说明

---

## Spec — 文档与计划

> [spec.md](../references/dimensions/spec.md)

- [ ] 覆盖 `requirements.md` AC，无 scope creep
- [ ] 实现符合 `design.md` 模块边界、接口与数据契约
- [ ] `test_plan.md` 关键/异常/边界有测试或等价验证
- [ ] 文档与代码不一致已判定责任方（代码 / 文档 / 需求待确认）
- [ ] 根因层级与改动层级一致；PATCH_RISK 可解释
- [ ] `dev_plan.md` 必要项已完成或说明豁免
- [ ] Agent NFR（超时/进程清理/交互阻断）已覆盖或豁免

---

## Security — 安全

> [security.md](../references/dimensions/security.md)

- [ ] 外部输入在信任边界（API/handler 入口）校验类型、长度、枚举、格式
- [ ] 鉴权/授权齐全：默认拒绝；含水平/垂直越权（IDOR）；敏感操作可审计
- [ ] 无注入：SQL/NoSQL/命令/模板/路径拼接；查询参数化、输出编码
- [ ] XSS/CSRF：无不可信 `innerHTML`/`dangerouslySetInnerHTML`/`eval`；框架转义或消毒
- [ ] 敏感信息未写入日志、响应体或客户端存储；API 响应字段裁剪（token/hash 等）
- [ ] 密钥/凭证非硬编码；`.env` 与明文 secret 未入库
- [ ] 新增依赖无已知可达 critical/high CVE；CORS/安全头/上传/Webhook 变更符合设计

### 深度分析（Security 维度）

- [ ] **输入信任边界**：是否所有外部输入（用户输入、LLM 输出、API 响应、文件内容）在使用前都经过校验？是否存在「信任 LLM 返回格式正确就直接用」的风险？
- [ ] **注入向量**：用户/LLM 输出是否拼入 SQL/命令/shell/模板/路径？是否存在路径遍历（`../`）或命令注入风险？
- [ ] **敏感数据泄露**：LLM prompt/response 中是否可能包含 token/密码/密钥？这些内容是否被记入日志或返回给客户端？

---

## Performance — 性能

> [performance.md](../references/dimensions/performance.md)

- [ ] 无 N+1、无界分页/拉取、全表扫描等已知反模式（热路径优先）
- [ ] 新 filter/sort 字段有索引或迁移说明（大数据量场景）
- [ ] 外部调用有超时；热路径有重试/熔断/限流（按 `design.md`）
- [ ] 无热路径同步阻塞（重型 CPU/正则/同步 IO）；缓存更新顺序与 TTL 合理
- [ ] 资源无泄露：连接/文件/线程/定时器正确关闭；连接池与重试有上限
- [ ] 大对象深拷贝、巨型 JSON 序列化、无界内存 Map 可控
- [ ] 前端路径（若涉及）：大包体/lazy、瀑布请求、首屏 LCP/INP 相关退化可解释

### 深度分析（Performance 维度）

- [ ] **LLM 调用成本**：是否新增不必要的 LLM 调用？prompt 长度是否可能失控（无界拼接历史消息/上下文）？
- [ ] **大对象处理**：是否对大列表/大字符串做全量操作（如全量 `split`/`join`/`json.dumps`）而非惰性/流式处理？

---

## 通用

- [ ] 主流程 + 异常流程；边界输入（空、0、负数、超长、非法枚举）处理；向后兼容
- [ ] 事务边界正确；幂等（重试/重复消息/重复提交）；缓存与 DB 一致性

## 深度审查完成检查（自检）

审查完成后，逐条确认以下问题是否都有答案：

- [ ] **数据流**：每个被修改函数的数据从入口到出口，是否追踪了所有可能的丢失点？
- [ ] **边界条件**：每个条件分支的边界输入（空值/零值/极值/非法值）是否都考虑了？
- [ ] **错误路径**：外部依赖（LLM/API/DB/文件系统）失效时，代码行为是否可接受？
- [ ] **兼容性**：修改现有行为是否影响旧调用方？是否有破坏性变更未说明？
- [ ] **系统性**：是否从功能整体视角扫描了所有环节的失效模式，而非逐函数点状看？

**如果以上任何一项答案为「否」→ 审查不合格，必须重新深挖对应维度。**

## Aidlc 收口

- [ ] Should Fix 逐条可执行含 id；需 Leader 本轮必改 → `leader_escalate`
