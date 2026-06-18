---
name: csv-row-counter
description: >-
  Count data rows in CSV files. Supports header detection, delimiter customization,
  and row filtering. Use when user asks to count rows, lines, or records in a CSV file.
  NOT for parsing or analyzing CSV content.
allowed_tools: [bash]
---

# CSV Row Counter

统计 CSV 文件的数据行数。

## 使用场景

当用户需要：
- 统计 CSV 文件有多少行数据
- 确认 CSV 文件的记录数
- 验证数据导入后的行数是否一致

## 执行方式

使用 `bash` 工具运行本 skill 目录下的脚本：

```bash
python scripts/count_rows.py <csv_file_path> [--delimiter <delim>] [--filter <column>=<value>]
```

### 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `csv_file_path` | 是 | CSV 文件路径 |
| `--delimiter` | 否 | 分隔符，默认逗号 `,` |
| `--filter` | 否 | 按条件过滤后计数，格式 `column=value` |

### 示例

```bash
# 统计普通 CSV 行数
python scripts/count_rows.py data.csv

# Tab 分隔的文件
python scripts/count_rows.py data.tsv --delimiter "\t"

# 统计 status=active 的行数
python scripts/count_rows.py users.csv --filter status=active
```

## 重要规则

- CSV 文件的首行始终为表头（header），统计行数时必须跳过首行
- 脚本自动处理编码（UTF-8 / GBK 自动探测）
- 空行不计入数据行数
- 过滤条件支持单列精确匹配

## 输出格式

```
文件: data.csv
总行数: 150
表头: id,name,email,status
数据行数: 149
```
