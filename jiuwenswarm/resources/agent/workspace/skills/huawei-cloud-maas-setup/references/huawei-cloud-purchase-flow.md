# 华为云 MaaS 购买流程详细步骤

## 官方文档

- 快速入门: https://support.huaweicloud.com/qs-maas/qs-maas-0001.html
- MaaS 访问授权: https://support.huaweicloud.com/permission-maas/maas-modelarts-0016.html
- API 调用规范: https://support.huaweicloud.com/model-call-maas/model-call-017.html
- 模型服务价格: https://support.huaweicloud.com/price-maas/price-maas-0001.html

## 关键页面 URL

| 页面 | URL |
|------|-----|
| MaaS 控制台首页 | https://console.huaweicloud.com/modelarts/#/model-studio/homepage |
| API Key 管理页面 | https://console.huaweaweicloud.com/modelarts/#/model-studio/authmanage |
| 在线推理（预置服务） | https://console.huaweicloud.com/modelarts/#/model-studio/deployment |
| 模型体验页面 | https://console.huaweicloud.com/modelarts/?#/model-studio/experience |

## 前置条件

1. 已注册华为账号并开通华为云
2. 已完成实名认证
3. 账号未处于欠费或冻结状态
4. 已配置 MaaS 访问授权（委托授权）

## 购买流程

### 步骤 1: 准备账号和权限

- 确认华为云账号已注册、已实名认证、未欠费
- 配置 MaaS 访问授权（委托授权），参考 https://support.huaweicloud.com/permission-maas/maas-modelarts-0016.html

### 步骤 2: 获取 API Key

- 访问 API Key 管理页面: https://console.huaweicloud.com/modelarts/#/model-studio/authmanage
- 点击「创建API Key」
- API Key 仅在创建时显示一次，必须立即复制保存
- API Key 创建后可能需要几分钟生效

### 步骤 3: 开通预置模型服务

> 该功能仅支持「西南-贵阳一」区域

- 在 MaaS 控制台左侧导航栏，选择「模型推理 > 在线推理」
- 在「预置服务」页签，选择 openPangu-2.0-Pro 模型服务
- 单击操作列的「开通服务」
- 在弹出框中勾选「我已阅读并同意上述说明，及《MaaS 模型即服务声明》」
- 单击「一键开通」
- 等待状态变为「开通」

### 步骤 4: 调用 API

#### OpenAI 兼容方式（推荐 jiuwenswarm 使用）

```python
from openai import OpenAI

base_url = "https://api.modelarts-maas.com/openai/v1"
api_key = "MAAS_API_KEY"

client = OpenAI(api_key=api_key, base_url=base_url)
response = client.chat.completions.create(
    model="openpangu-2.0-pro",
    messages=[
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "介绍下你自己"},
    ]
)
print(response.choices[0].message.content)
```

#### 直接 API 方式

```
POST https://api.modelarts-maas.com/v2/chat/completions
Headers:
  Content-Type: application/json
  Authorization: Bearer <api_key>
Body:
  {"model": "openpangu-2.0-pro", "messages": [...]}
```

## 可用模型

| 模型名称 | model 参数值 |
|----------|-------------|
| openPangu-2.0-Pro | `openpangu-2.0-pro` |
| DeepSeek | 见控制台调用说明 |
| 千问 (Qwen) | 见控制台调用说明 |
| GLM | 见控制台调用说明 |
| Kimi | 见控制台调用说明 |

> 更多模型及对应 API 参考: https://support.huaweicloud.com/model-call-maas/model-call-017.html

## 计费说明

- 开通后调用以实际用量进行扣费
- 未使用时不会产生费用
- 不同模型计费方式有所区别，详见: https://support.huaweicloud.com/price-maas/price-maas-0001.html

## 常见问题

### API Key 创建后需要等待多久才能生效？

API Key 在创建后不会立即生效，通常需要等待几分钟才能生效。

### 如何查看更多模型？

前往控制台「模型推理 > 在线推理」页面查看所有预置服务，或参考 API 调用规范文档。
