---
name: template-renderer
description: >-
  Render a text template by substituting {{variable}} placeholders with given values.
  Use when user asks to render, fill, or instantiate a template file with variables.
  NOT for HTML page generation or complex logic templating engines.
allowed_tools: [bash]
---

# Template Renderer

渲染文本模板：将 `{{variable}}` 占位符替换为给定值。

## 执行方式

```bash
python scripts/render.py <template_file> [--var KEY=VAL ...]
```

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `template_file` | 是 | 模板文件路径（需在模板根目录下） |
| `--var` | 否 | 变量赋值，可重复，格式 `KEY=VAL` |

### 示例

```bash
# 渲染并替换变量
python scripts/render.py welcome.tpl --var name=World --var service=Jiuwen

# 仅渲染，保留未赋值的占位符原样
python scripts/render.py greeting.tpl --var name=Alice
```

## 占位符规则

- 形如 `{{name}}` 的占位符会被替换为对应 `--var name=VALUE` 的值
- 未提供值的占位符保持原样输出
- 支持同一变量在模板中出现多次

## 输出格式

直接打印渲染后的文本到标准输出。
