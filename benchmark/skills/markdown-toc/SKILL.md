---
name: markdown-toc
description: >-
  Generate a hierarchical Table of Contents (TOC) from a Markdown document.
  Extracts headings (h1-h6), builds nested structure, and creates anchor links.
  Use when user asks to generate a TOC, outline, or navigation index from a Markdown file.
  NOT for generating Markdown content itself.
---

# Markdown TOC Generator

从 Markdown 文档自动生成层级化目录。

## 使用场景

当用户需要：
- 为长文档生成目录索引
- 生成文档大纲
- 创建可点击的导航链接

## 执行流程

1. **读取文件** — 使用 `bash` 工具读取用户指定的 Markdown 文件
2. **解析标题** — 提取所有 `#` ~ `######` 标题行
3. **生成锚点** — 将标题转为 URL 安全的锚点（小写，空格转 `-`，去除特殊字符）
4. **构建层级** — 根据标题级别构建嵌套结构
5. **输出目录** — 生成缩进的 Markdown 链接列表

## 示例

### 输入

```markdown
# 项目概述
## 背景
### 市场需求
### 技术方案
## 目标
# 实施计划
## 第一阶段
## 第二阶段
```

### 输出

```markdown
## 目录

- [项目概述](#项目概述)
  - [背景](#背景)
    - [市场需求](#市场需求)
    - [技术方案](#技术方案)
  - [目标](#目标)
- [实施计划](#实施计划)
  - [第一阶段](#第一阶段)
  - [第二阶段](#第二阶段)
```

## 锚点生成规则

1. 标题文本转为小写
2. 空格替换为 `-`
3. 移除 `!@#$%^&*()+=[]{}|;:'",.<>?/` 等特殊字符
4. 连续 `-` 合并为单个
5. 首尾 `-` 去除

## 注意事项

- 代码块（```）内的 `#` 不识别为标题
- 重复标题自动添加序号后缀（`-1`, `-2`）
- 最大支持 6 级标题（h1-h6）
- 空文档返回提示"文档中未找到标题"
