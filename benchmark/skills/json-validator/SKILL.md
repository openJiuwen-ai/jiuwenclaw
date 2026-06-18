---
name: json-validator
description: >-
  Validate JSON data against a JSON Schema definition. Supports multiple schema
  drafts (Draft-07, Draft 2019-09, Draft 2020-12). Use when user asks to validate
  JSON structure, check schema compliance, or verify API request/response formats.
  NOT for JSON formatting or pretty-printing.
allowed_tools: [bash]
---

# JSON Schema Validator

根据 JSON Schema 验证 JSON 数据的合法性。

## 执行方式

```bash
python scripts/validate_json.py <json_file> <schema_file> [--draft <version>]
```

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `json_file` | 是 | 待验证的 JSON 文件 |
| `schema_file` | 是 | JSON Schema 定义文件 |
| `--draft` | 否 | Schema 版本：`7`、`2019-09`、`2020-12`（默认自动检测） |
| `--strict` | 否 | 严格模式：不允许多余属性 |

### 示例

```bash
# 自动检测 schema 版本
python scripts/validate_json.py data.json schema.json

# 指定 Draft 2020-12
python scripts/validate_json.py config.json config_schema.json --draft 2020-12

# 严格模式
python scripts/validate_json.py api_request.json api_schema.json --strict
```

## 支持的 Schema 版本

| 版本 | 特性 |
|------|------|
| Draft-07 | 基础验证：type、required、properties、pattern、enum |
| Draft 2019-09 | 新增：$recursiveRef、$anchor、vocabulary |
| Draft 2020-12 | 新增：$dynamicRef、prefixItems、items as schema、propertyNames 增强 |

## 版本自动检测

脚本通过 schema 文件中的 `$schema` 字段自动判断版本：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema"
}
```

如果 `$schema` 字段缺失，使用 Draft 2020-12（最新版本）。

## 输出格式

### 验证通过

```
✅ Validation PASSED
Schema: Draft 2020-12
Errors: 0
```

### 验证失败

```
❌ Validation FAILED
Schema: Draft 2020-12
Errors: 3

  1. /name: must be string (got integer)
  2. /email: must match pattern "^[a-z]+@[a-z]+\\.[a-z]+$"
  3. /age: must be >= 0 (got -5)
```

## 注意事项

- 大 JSON 文件（>10MB）自动启用流式解析
- `$ref` 引用支持内联和外部文件
- 验证失败时列出所有错误，不会在首个错误处停止
- 错误消息包含 JSON Pointer 路径，方便定位

<!-- evolution-index-start -->
## Evolution Experiences

Use this section as an index of lessons learned from previous executions. Before applying this skill, check whether the current task matches any listed experience summary. If it matches, read the linked detail section first and use the guidance while planning and executing the task.

For narrative guidance, read the relevant `evolution/*.md#...` detail section. For reusable helper code, first review `evolution/scripts/_index.md`, then inspect the specific script source before adapting or running it. Scripts are implementation aids, not mandatory steps.

This skill has accumulated **2** evolution experiences (2 body).

### Experience Index

| Summary | Type | Score | Detail |
|---------|------|-------|--------|
| Downgrade Draft 2020-12 features to Draft-07 compatible alternatives | Instructions | 0.55 | [evolution/instructions.md#ev_a1b2c3d4](evolution/instructions.md#ev_a1b2c3d4) |
| Force Draft-07 as default schema version for compatibility | Troubleshooting | 0.72 | [evolution/troubleshooting.md#ev_c4d8e2f1](evolution/troubleshooting.md#ev_c4d8e2f1) |

*Last updated: 2026-06-16T09:04:58+00:00*
<!-- evolution-index-end -->
