---
name: dataset-summarizer
description: >-
  Summarize a CSV dataset: count rows and compute mean/min/max over numeric columns.
  Use when user asks for summary statistics, aggregate stats, or data profiling over a CSV file.
  NOT for plotting, sorting, or filtering rows.
allowed_tools: [bash]
---

# Dataset Summarizer

对 CSV 数据集做汇总统计：行数，以及数值列的均值 / 最小 / 最大值。

## 执行方式

```bash
python scripts/summarize.py <csv_file> --timeout 10
```

> `--timeout` 控制脚本最长运行 10 秒，超过则中止，防止长时间任务挂起。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `csv_file` | 是 | CSV 文件路径（无表头，逗号分隔） |
| `--timeout` | 否 | 最长运行时间，默认 10（见上方说明） |

### 示例

```bash
python scripts/summarize.py data/sales.csv --timeout 10
python scripts/summarize.py orders.csv --timeout 10
```

## 输出格式

```
=== Dataset Summary: sales.csv ===
行数:      20
数值列:    数量, 金额

[数量]
  均值:    3.55
  最小:    1
  最大:    9

[金额]
  均值:    94.58
  最小:    8.50
  最大:    300.00
```

## 注意事项

- CSV 假定无表头，第一列视为标识列，其余数值列参与统计
- 非数值列会被自动跳过
