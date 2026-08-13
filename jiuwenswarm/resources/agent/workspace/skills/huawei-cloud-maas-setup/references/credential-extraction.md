# API 凭证提取策略

## 需要提取的凭证

| 凭证 | 来源 | 固定/动态 |
|------|------|----------|
| api_key | API Key 管理页面创建 | 动态（用户每次创建不同） |
| api_base | OpenAI 兼容接口地址 | 固定: `https://api.modelarts-maas.com/openai/v1` |
| model_name | 预置服务页面 | 默认: `openpangu-2.0-pro` |
| model_provider | 接口兼容类型 | 固定: `openai` |

## 自动提取策略

### API Key 提取

1. 委派 browser_agent 导航到 API Key 管理页面
2. 探测页面是否已有 API Key
3. 如果没有，点击「创建API Key」按钮
4. 等待创建完成，从页面中提取新创建的 API Key 值

**关键风险**：API Key 仅在创建时显示一次。如果 browser_agent 未能在创建瞬间捕获，则需要用户重新创建。

### 降级方案

如果自动提取失败：
1. 通过 `ask_user_question` 请求用户手动复制粘贴 API Key
2. 提供手动创建的详细步骤说明

## 安全注意事项

- API Key 是敏感信息，不在聊天消息中明文显示完整值
- 展示时仅显示前后 4 位，中间用 `****` 替代
- 写入配置时通过 jiuwenswarm 的加密机制存储
- 不要将 API Key 写入日志或临时文件

## 页面特征参考

> 华为云控制台 UI 可能随时间变化，以下特征供参考，实际以页面为准。

### API Key 管理页面特征

- 页面 URL: `https://console.huaweicloud.com/modelarts/#/model-studio/authmanage`
- 关键元素: 「创建API Key」按钮
- 创建后: 页面应显示新创建的 API Key 值（通常在弹窗或提示中）
- 已有 Key: 页面应显示已存在的 API Key 列表

### 在线推理页面特征

- 页面 URL: `https://console.huaweicloud.com/modelarts/#/model-studio/deployment`
- 关键元素: 「预置服务」页签
- 模型列表: 包含 openPangu-2.0-Pro 等模型
- 操作按钮: 「开通服务」/ 「调用说明」
- 状态标识: 「开通」/ 「未开通」
