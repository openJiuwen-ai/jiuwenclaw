---
name: xiaoyi-doc-convert
description: |
  统一的文档格式转换工具。
  支持 Word (DOCX/DOC)、PowerPoint (PPTX/PPT)、Excel (XLSX/XLS)、PDF、Markdown (MD)、HTML 等多种格式互转。

  适用场景：
  - 在多种格式间转换文档（HTML↔PDF、Word↔PDF、Markdown→Word 等）
  - 批量或单文件转换
  - 支持多种格式描述方式，如 excel/xls/xlsx、doc/docx/word、md/markdown

  触发方式：
  - 用户说 "转换文档格式"、"docx转pdf"、"html转ppt"、"excel转pdf"、"md转word"
  - 使用 "/xiaoyi-doc-convert <文件> <目标格式>" 格式

  关键触发词：
  - 通用：文档转换、格式转换、doc convert、file convert
  - PDF：转pdf、转PDF、export to pdf、保存为pdf
  - Word：转word、转doc、转docx、word转换、doc转pdf、docx转pdf
  - Excel：转excel、转xls、转xlsx、xls转pdf、xlsx转pdf、excel转pdf、表格转pdf
  - PPT：转ppt、转pptx、转powerpoint、ppt转pdf、pptx转pdf、幻灯片转pdf
  - Markdown：md转word、md转pdf、markdown转word、markdown转pdf、md转docx、markdown转excel
  - HTML：html转pdf、网页转pdf、html转word、html转ppt
allowed-tools: Read, Bash, Write
---

# xiaoyi-doc-convert - 统一文档格式转换工具

一个整合多种文档转换能力的统一工具，简化格式转换操作。

## 功能说明

### 支持的转换矩阵

本工具支持多种格式的描述方式（如：Excel/xls/xlsx、Word/doc/docx、md/markdown），会自动识别并处理。

| 源格式 | 目标格式 | 实现方式 | 说明 |
|--------|----------|----------|------|
| HTML | PDF | 直接转换 | 使用 Playwright 渲染 |
| HTML | MD | 直接转换 | 使用 html2text |
| HTML | PPTX | 两步转换 | HTML → PDF → PPTX |
| DOCX / DOC / Word | PDF | 直接转换 | 使用 LibreOffice |
| PPTX / PPT / PowerPoint | PDF | 直接转换 | 使用 LibreOffice |
| PDF | PPTX | 直接转换 | 使用 LibreOffice (impress_pdf_import) |
| Markdown / MD | DOCX / Word | 直接转换 | 使用 md2doc |
| Markdown / MD | PDF | 两步转换 | Markdown → DOCX → PDF (使用 md2doc + LibreOffice) |
| Markdown / MD | XLSX / Excel | 直接转换 | 使用 md2doc |
| DOC (旧版 Word) | DOCX | 直接转换 | 使用 LibreOffice |
| PPT (旧版 PowerPoint) | PPTX | 直接转换 | 使用 LibreOffice |
| DOC (旧版 Word) | PDF | 直接转换 | 使用 LibreOffice |
| PPT (旧版 PowerPoint) | PDF | 直接转换 | 使用 LibreOffice |

### 不支持的转换

- PDF → Word (PDF 转 DOCX) - 暂不支持
- 其他未在矩阵中列出的转换组合

## 脚本结构

```
xiaoyi-doc-convert/
├── SKILL.md                  # 本说明文档
└── scripts/
    ├── __init__.py           # 包初始化
    ├── main.py               # 主入口脚本
    ├── converter.py          # 转换调度核心
    ├── routing.py            # 转换路径规划
    ├── adapters/             # 外部工具适配器
    │   ├── __init__.py
    │   └── soffice_adapter.py    # DOCX/PPTX ↔ PDF (LibreOffice)
    ├── html2pdf/             # 内部集成的 html2pdf 模块
    │   ├── __init__.py
    │   ├── core.py           # HTML 转 PDF 核心实现
    │   └── pdfmerge.py       # PDF 合并工具
    └── md2doc/               # 内部集成的 md2doc 模块
        ├── converter.py      # Markdown 转换统一入口
        ├── config/           # 配置和模板文件
        ├── converters/       # 格式转换器
        ├── filters/          # 内容过滤器
        ├── utils/            # 工具函数
        └── ...
```

### 模块依赖关系

```
main.py
├── converter.py
│   ├── routing.py (转换路径规划)
│   ├── html2pdf/core.py (HTML → PDF)
│   ├── md2doc/converter.py (Markdown → DOCX/PDF/XLSX)
│   └── adapters/soffice_adapter.py (DOCX/PPTX ↔ PDF)
```

## 用法

### 执行命令

所有操作都在 skill 目录的 `scripts/` 子目录中执行：

```bash
cd <skill_dir>/scripts && python main.py <input_file> <target_format> [options]
```

其中 `<skill_dir>` 是 xiaoyi-doc-convert skill 的根目录。

### 基本用法

```bash
# HTML 转 PDF
cd <skill_dir>/scripts && python main.py report.html pdf

# HTML 转 Markdown
cd <skill_dir>/scripts && python main.py page.html md

# HTML 转 PPTX (自动使用 html→pdf→pptx 路径)
cd <skill_dir>/scripts && python main.py slides.html pptx

# DOCX 转 PDF (Word 转 PDF)
cd <skill_dir>/scripts && python main.py document.docx pdf

# Markdown 转 Word (MD 转 DOCX)
cd <skill_dir>/scripts && python main.py notes.md docx

# Markdown 转 PDF (MD 转 PDF)
cd <skill_dir>/scripts && python main.py notes.md pdf

# Markdown 转 Excel (MD 转 XLSX)
cd <skill_dir>/scripts && python main.py table.md xlsx

# 旧版 Word 转新版 DOCX (DOC 转 DOCX)
cd <skill_dir>/scripts && python main.py old_document.doc docx

# 旧版 PowerPoint 转新版 PPTX (PPT 转 PPTX)
cd <skill_dir>/scripts && python main.py old_slides.ppt pptx

# Excel 转 PDF (XLSX 转 PDF)
cd <skill_dir>/scripts && python main.py data.xlsx pdf

# PDF 转 PPTX
cd <skill_dir>/scripts && python main.py slides.pdf pptx
```

### 自定义页面尺寸 (HTML 相关转换)

默认使用 A4 尺寸 (21cm x 29.7cm)。

```bash
# 默认 A4 尺寸
cd <skill_dir>/scripts && python main.py report.html pdf

# 指定 16:9 屏幕尺寸 (用于演示文稿)
cd <skill_dir>/scripts && python main.py slides.html pptx --width 33.9cm --height 19.1cm
```

### 查看支持的转换

```bash
cd <skill_dir>/scripts && python main.py --list
```

输出示例：
```
Supported Conversions:
==================================================
Source       -> Target       | Path
--------------------------------------------------
doc          -> pdf          | soffice
docx         -> pdf          | soffice
html         -> md           | html2md
html         -> pdf          | html2pdf
html         -> pptx         | html2pdf -> soffice
md           -> docx         | md2doc
md           -> pdf          | md2doc -> soffice
md           -> xlsx         | md2doc
pdf          -> pptx         | soffice
ppt          -> pdf          | soffice
pptx         -> pdf          | soffice
==================================================
```

## 参数说明

| 参数 | 位置 | 必填 | 说明 |
|------|------|------|------|
| `input` | 第1个 | 是 | 输入文件路径 |
| `target` | 第2个 | 是 | 目标格式 (pdf/docx/pptx/xlsx) |
| `--width` | 选项 | 否 | PDF 页面宽度 (HTML 转换时有效) |
| `--height` | 选项 | 否 | PDF 页面高度 (HTML 转换时有效) |
| `-l, --list` | 标志 | 否 | 显示支持的转换列表 |

## 转换路径说明

### 单步转换

直接使用内部模块完成转换：

- `html → pdf`: 使用内部 html2pdf 模块 (Playwright)
- `docx → pdf`: 调用 soffice (LibreOffice)
- `md → docx/xlsx`: 使用内部 md2doc 模块 (Pandoc)

### 多步转换

自动规划为多步骤转换，使用中间格式：

- `html → pptx`:
  1. HTML → PDF (内部 html2pdf)
  2. PDF → PPTX (soffice)
  3. 自动清理中间 PDF 文件

- `md → pdf`:
  1. Markdown → DOCX (内部 md2doc)
  2. DOCX → PDF (soffice)
  3. 自动清理中间 DOCX 文件

## 依赖要求

### 必需依赖

| 转换类型 | 依赖 | 安装命令                      |
|----------|------|---------------------------|
| DOCX/PPTX 相关 | LibreOffice | 系统包管理器安装                  |
| Markdown 相关 | Pandoc + Python 依赖 | 见 md2doc skill            |

### 检查依赖

适配器会在运行时检查依赖是否可用，如果缺少依赖会提示具体安装方法。

## 错误处理

### 常见错误

| 错误信息 | 原因 | 解决方法 |
|----------|------|----------|
| "soffice command not found" | LibreOffice 未安装 | 安装 LibreOffice 并确保 soffice 在 PATH 中 |
| "Conversion from X to Y is not supported" | 不支持的转换组合 | 使用 `--list` 查看支持的转换 |
| "Input file not found" | 输入文件不存在 | 检查文件路径 |

## 文件名约定

转换后的输出文件遵循以下命名规则：
- **文件名保持不变，仅扩展名修改为对应的目标格式**
- 示例：`document.doc` → `document.pdf`，`slides.ppt` → `slides.pdf`

## 注意事项

1. **文件名保持**: 转换后的文件保持原文件名不变，仅修改扩展名
   - 示例：`report.docx` → `report.pdf`，`data.md` → `data.docx`
2. **临时文件**: 多步转换的中间文件自动创建和清理
3. **输出目录**: 自动创建不存在的输出目录
4. **文件覆盖**: 输出文件已存在时会自动覆盖
