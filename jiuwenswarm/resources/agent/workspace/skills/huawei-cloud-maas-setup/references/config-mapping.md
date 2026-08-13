# 凭证到 jiuwenswarm 配置的映射

## 配置文件位置

| 文件 | 路径 | 用途 |
|------|------|------|
| 环境变量 | `~/.jiuwenswarm/config/.env` | 存储 API_KEY、API_BASE 等环境变量 |
| 主配置 | `~/.jiuwenswarm/config/config.yaml` | 存储 models.defaults 模型配置 |

## 映射关系

| 华为云 MaaS 凭证 | .env 变量 | config.yaml 字段 |
|-----------------|----------|-----------------|
| API Key | `API_KEY` | `models.defaults[0].model_client_config.api_key` |
| API 地址 | `API_BASE` | `models.defaults[0].model_client_config.api_base` |
| 模型名称 | `MODEL_NAME` | `models.defaults[0].model_client_config.model_name` |
| 接入方式 | `MODEL_PROVIDER` | `models.defaults[0].model_client_config.client_provider` |

## .env 文件写入格式

```ini
API_BASE="https://api.modelarts-maas.com/openai/v1"
API_KEY="<用户的 API Key>"
MODEL_NAME="openpangu-2.0-pro"
MODEL_PROVIDER="openai"
```

## config.yaml 写入格式

config.yaml 中的 `models.defaults` 列表会被替换为：

```yaml
models:
  defaults:
    - model_client_config:
        api_base: ${API_BASE}
        api_key: ${API_KEY}
        model_name: ${MODEL_NAME}
        client_provider: ${MODEL_PROVIDER}
        timeout: 360
        verify_ssl: true
        custom_headers: {}
      model_config_obj:
        temperature: 0.95
      is_default: true
```

> `${API_BASE}` 等是环境变量占位符，由 `.env` 文件注入。config.yaml 中不直接写值，而是引用环境变量。

## 配置写入机制

配置写入通过 `scripts/update_jiuwenswarm_config.py` 脚本完成，该脚本：

1. 更新 `.env` 文件中的 4 个环境变量
2. 通过 `update_default_models_in_config()` 更新 config.yaml 的 models.defaults 列表
3. API Key 会通过 jiuwenswarm 的加密机制加密存储（如果 crypto_provider 可用）

## 验证机制

配置写入后，通过 `scripts/validate_config.py` 验证：

1. 读取当前配置中的 api_base、api_key、model_name
2. 向华为云 MaaS API 发送一个简单的测试请求
3. 返回验证结果（成功/失败 + 错误信息）

> 注意：API Key 创建后可能需要几分钟生效，首次验证可能失败。
