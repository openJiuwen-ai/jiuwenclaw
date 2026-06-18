---
name: currency-converter
description: >-
  Convert between currencies using live exchange rates from an API.
  Supports major currencies (USD, EUR, CNY, JPY, GBP, etc.).
  Use when user asks to convert money between currencies or check exchange rates.
  NOT for cryptocurrency or stock prices.
allowed_tools: [bash]
---

# Currency Converter

使用实时汇率进行货币换算。

## 执行方式

```bash
python scripts/convert_currency.py <amount> <from_currency> <to_currency>
```

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `amount` | 是 | 金额 |
| `from_currency` | 是 | 源货币代码（ISO 4217，如 USD、CNY） |
| `to_currency` | 是 | 目标货币代码 |

### 示例

```bash
python scripts/convert_currency.py 100 USD CNY
python scripts/convert_currency.py 5000 JPY EUR
python scripts/convert_currency.py 250 GBP USD
```

## 数据源

- **主 API**: `https://api.exchangerate-api.com/v4/latest/{base}`
- **备用 API**: `https://open.er-api.com/v6/latest/{base}`
- 汇率每 15 分钟自动刷新
- 支持 150+ 种货币

## 输出格式

```
100.00 USD = 724.50 CNY
汇率: 1 USD = 7.245 CNY
更新时间: 2026-06-15 14:30:00 UTC
数据源: exchangerate-api.com
```

## Fallback 策略

当 API 不可用时（网络超时、服务不可用），系统应：
1. 尝试备用 API
2. 若两个 API 均不可用，提示用户"汇率服务暂时不可用，请稍后重试"
3. **不要使用缓存的旧汇率** — 汇率波动大，过时数据可能误导用户

## 注意事项

- 汇率为中间价，实际银行汇率可能有 1-3% 的价差
- 大额换算（>100万）添加 ⚠️ 提示"建议使用银行实时报价"
- 不支持已废弃的货币代码（如 ZWL 旧版）

<!-- evolution-index-start -->
## Evolution Experiences

Use this section as an index of lessons learned from previous executions. Before applying this skill, check whether the current task matches any listed experience summary. If it matches, read the linked detail section first and use the guidance while planning and executing the task.

For narrative guidance, read the relevant `evolution/*.md#...` detail section. For reusable helper code, first review `evolution/scripts/_index.md`, then inspect the specific script source before adapting or running it. Scripts are implementation aids, not mandatory steps.

This skill has accumulated **1** evolution experiences (1 body).

### Experience Index

| Summary | Type | Score | Detail |
|---------|------|-------|--------|
| Provide fallback exchange rate when API is unavailable | Troubleshooting | 0.65 | [evolution/troubleshooting.md#ev_7a3f91b2](evolution/troubleshooting.md#ev_7a3f91b2) |

*Last updated: 2026-06-16T09:04:58+00:00*
<!-- evolution-index-end -->
