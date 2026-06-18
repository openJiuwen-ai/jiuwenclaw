---
name: weather-api-query
description: >-
  Query current weather and forecasts for any city using OpenWeatherMap API.
  Returns temperature, humidity, wind speed, and conditions.
  Use when user asks about weather, temperature, or forecasts for a specific city.
  NOT for historical weather data or severe weather alerts.
allowed_tools: [bash]
---

# Weather API Query

查询城市实时天气和天气预报。

## 执行方式

```bash
python scripts/query_weather.py <city_name> [--api-key <key>] [--units metric|imperial] [--forecast <days>]
```

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `city_name` | 是 | 城市名称（中英文均可） |
| `--api-key` | 否 | OpenWeatherMap API Key（默认从环境变量 `OWM_API_KEY` 读取） |
| `--units` | 否 | 温度单位：`metric`（摄氏度，默认）、`imperial`（华氏度） |
| `--forecast` | 否 | 查询未来 N 天预报（1-5） |

### 示例

```bash
# 查询北京当前天气
python scripts/query_weather.py Beijing

# 查询上海未来 3 天预报
python scripts/query_weather.py Shanghai --forecast 3

# 使用华氏度
python scripts/query_weather.py "New York" --units imperial
```

## API 依赖

本 skill 依赖 **OpenWeatherMap API**：
- 端点：`https://api.openweathermap.org/data/2.5/weather`
- 需要有效的 API Key
- 免费套餐限制：60 次/分钟、1000 次/天
- API Key 申请地址：https://openweathermap.org/api

## 输出格式

### 当前天气

```
🌤 北京 当前天气
━━━━━━━━━━━━━━━━━━━
温度:    28°C (体感 32°C)
天气:    多云
湿度:    65%
风速:    3.5 m/s (东南风)
气压:    1013 hPa
能见度:  10 km
更新时间: 2026-06-15 14:30 UTC
```

### 预报

```
📅 上海 未来 3 天预报
━━━━━━━━━━━━━━━━━━━
06-16: 🌤 多云  22~28°C  降水概率 10%
06-17: 🌧 小雨  19~25°C  降水概率 80%
06-18: ☀️ 晴    24~31°C  降水概率 5%
```

## 错误处理

| 错误 | 原因 | 处理 |
|------|------|------|
| 401 Unauthorized | API Key 无效或过期 | 提示用户检查 OWM_API_KEY |
| 404 Not Found | 城市名无法识别 | 建议使用英文城市名 |
| 429 Too Many Requests | 超出免费套餐限额 | 提示等待或升级 API 套餐 |
| 503 Service Unavailable | API 服务端不可用 | 提示用户稍后重试 |
| Connection Error | 网络不可达 | 检查网络连接 |

## 注意事项

- API Key 不应硬编码在对话中，优先使用环境变量
- 天气数据每 10 分钟更新一次（API 端限制）
- 极端天气预警需额外 API（不在免费范围内）
