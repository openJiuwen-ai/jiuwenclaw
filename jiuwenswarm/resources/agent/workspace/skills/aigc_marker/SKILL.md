---
name: aigc_marker
description: 为已存在的 DOCX、PDF、Excel、PPT、MD、HTML (.html, .htm)、图片、音频及视频文件添加 AIGC 标识
version: 1.8.0
entry: scripts/main.py
author: celia
---

# aigc_marker

为已存在的 Word、PDF、Excel、PowerPoint、Markdown、HTML (.html, .htm)、图片、音频及视频文件添加 AIGC（AI生成内容）标识。

## 功能

- **DOCX**: 为 Word 文档添加自定义属性 "AIGC"
- **PDF**: 为 PDF 文档添加 AIGC 元数据
- **Excel**: 为 Excel 文件添加自定义文档属性
- **PPT**: 为 PowerPoint 演示文稿添加自定义属性 "AIGC"
- **MD**: 为 Markdown 文件添加 YAML 前置元数据
- **HTML (.html, .htm)**: 在 HTML 文件正文末尾添加 `<p>内容由AI生成</p>` 段落
- **Image (JPG/JPEG/PNG/WebP/HEIC/HEIF)**: 添加 EXIF/tEXt 隐式标识 + 右下角"AI生成"显式水印
- **Audio (WAV/MP3/OGG/FLAC/M4A)**: 添加 AIGC 元数据（隐式）+ 末尾摩斯码节奏标识（显式）
- **Video (MP4/FLV/MKV/AVI)**: 添加 AIGC 元数据（隐式）+ 全程右下角 "AI生成" 水印（显式，drawtext 重编码；AVI 使用 mpeg4 编码器）

## 脚本结构

```
scripts/
├── main.py                    # 入口脚本，接收命令行参数，调用对应装饰器
├── decorators/                # AIGC 装饰器模块
│   ├── __init__.py           # 包导出
│   ├── aigc_marker.py        # 装饰器类实现
│   └── html_decorator.py     # HTML 文件装饰器实现
├── docx_extend/              # DOCX 扩展功能（精简版）
│   ├── api.py                # DocumentExtend 类
│   ├── opc/                  # OPC 包处理
│   │   ├── customprops.py    # CustomProperties 类
│   │   └── parts/            # 部件定义
│   │       └── customprops.py # CustomPropertiesPart 类
│   └── oxml/                 # XML 元素定义
│       └── customprops.py    # CT_CustomProperties 等类
├── ppt_extend/               # PPT 扩展功能（精简版）
│   ├── api.py                # PresentationExtend 类
│   ├── opc/                  # OPC 包处理
│   │   ├── customprops.py    # CustomProperties 类
│   │   └── parts/            # 部件定义
│   │       └── customprops.py # CustomPropertiesPart 类
│   └── oxml/                 # XML 元素定义
│       ├── __init__.py       # 命名空间注册
│       └── customprops.py    # CT_CustomProperties 等类
├── fonts/                     # 字体文件（鸿蒙黑体）
│   ├── __init__.py           # 字体路径工具
│   └── HarmonyOS_Sans_SC_Bold.ttf  # 自带字体（手动放置）
└── oxml/                     # DOCX XML 元素定义
    └── customprops.py        # CT_CustomProperties 等类
```

### 核心模块

- **`scripts/main.py`** - 主入口，解析文件类型，调用对应装饰器
- **`scripts/decorators/aigc_marker.py`** - 包含以下装饰器类：
  - `DocxAigcDecorator` - Word 文档 AIGC 标记
  - `PdfAigcDecorator` - PDF 文档 AIGC 标记
  - `ExcelAigcDecorator` - Excel 文件 AIGC 标记
  - `HtmlAigcDecorator` - HTML 文件 AIGC 标记
  - `PptAigcDecorator` - PowerPoint 演示文稿 AIGC 标记
  - `MdAigcDecorator` - Markdown 文件 AIGC 标记
  - `ImageAigcDecorator` - 图片文件 AIGC 标记

### 依赖关系

```
main.py
└── decorators/aigc_marker.py
    ├── decorators/html_decorator.py
    ├── docx_extend/api.py
    │   └── docx_extend/opc/parts/customprops.py
    │       ├── docx_extend/opc/customprops.py
    │       └── docx_extend/oxml/customprops.py
    ├── ppt_extend/api.py
    │   └── ppt_extend/opc/parts/customprops.py
    │       ├── ppt_extend/opc/customprops.py
    │       └── ppt_extend/oxml/customprops.py
    ├── pypdf (第三方库)
    └── openpyxl (第三方库)
```

## 用法

### 执行命令

```bash
cd <skill_dir>/scripts && python main.py <文件路径>
```

模型会自动将 `<skill_dir>` 替换为实际的 skill 路径。

### 命令行用法

```bash
/aigc_marker <文件路径>
```

### 支持的文件格式

| 文件扩展名 | 说明 |
|-----------|------|
| `.docx` | Microsoft Word 文档 |
| `.pdf` | PDF 文档 |
| `.xlsx` | Microsoft Excel 工作簿 |
| `.pptx` | Microsoft PowerPoint 演示文稿 |
| `.md` | Markdown 文档 |
| `.html` | HTML 网页文件 |
| `.htm` | HTML 网页文件 |
| `.jpg` / `.jpeg` | JPEG 图片 |
| `.png` | PNG 图片 |
| `.webp` | WebP 图片 |
| `.heic` / `.heif` | HEIF/HEIC 图片 |
| `.wav` | WAV 音频 |
| `.mp3` | MP3 音频 |
| `.ogg` | OGG Vorbis 音频 |
| `.flac` | FLAC 无损音频 |
| `.m4a` | M4A / AAC 音频 |
| `.mp4` | MP4 视频 |
| `.flv` | FLV 视频 |
| `.mkv` | MKV 视频 |
| `.avi` | AVI 视频 |

## 参数说明

| 参数 | 类型 | 是否必填 | 说明 |
|------|------|----------|------|
| `文件路径` | string | 是 | 目标文件路径（支持 `.docx`、`.pdf`、`.xlsx`、`.pptx`、`.md`、`.html`、`.htm`、`.jpg`、`.png`、`.webp`、`.heic`、`.heif`、`.wav`、`.mp3`、`.ogg`、`.flac`、`.m4a`、`.mp4`、`.flv`、`.mkv`、`.avi`） |
| `--skip-visible` | flag | 否 | 跳过添加显式标识，仅添加隐式元数据 |

## AIGC 签名格式

生成的 AIGC 签名包含以下字段（JSON 格式）：

```json
{
  "Label": "AIGC",
  "ContentProducer": "AI Assistant",
  "ProduceID": "<uuid>",
  "ReservedCode1": "<content_hash>",
  "ContentPropagator": "",
  "PropagateID": "",
  "Timestamp": "<iso_timestamp>"
}
```

## 使用示例

### 命令行风格

```bash
# 为 Word 文档添加 AIGC 标记（默认添加显式和隐式标识）
/aigc_marker document.docx

# 为 PDF 文档添加 AIGC 标记
/aigc_marker report.pdf

# 为 Excel 文件添加 AIGC 标记
/aigc_marker data.xlsx

# 为 PowerPoint 演示文稿添加 AIGC 标记
/aigc_marker presentation.pptx

# 为 Markdown 文件添加 AIGC 标记
/aigc_marker notes.md

# 为 HTML 文件添加 AIGC 标记
/aigc_marker page.html
/aigc_marker page.htm

# 为 JPG 图片添加 AIGC 标记
/aigc_marker photo.jpg

# 为 PNG 图片添加 AIGC 标记
/aigc_marker image.png

# 为 MP3 音频添加 AIGC 标记
/aigc_marker audio.mp3

# 为 M4A 音频添加 AIGC 标记
/aigc_marker audio.m4a

# 为 WAV 音频添加 AIGC 标记
/aigc_marker audio.wav

# 为 MP4 视频添加 AIGC 标记
/aigc_marker video.mp4

# 为 AVI 视频添加 AIGC 标记
/aigc_marker video.avi

# 仅添加隐式视频元数据（跳过水印重编码，更快）
/aigc_marker video.mp4 --skip-visible

# 仅添加隐式标识（不显示显式水印）
/aigc_marker document.docx --skip-visible
```

### 函数调用风格

```python
from decorators import docx_aigc_decorator

# 默认：添加显式和隐式标识
docx_aigc_decorator.decorate("document.docx", "content_hash", "req123")

# 仅添加隐式标识
docx_aigc_decorator.decorate("document.docx", "content_hash", "req123", add_visible_mark=False)
```

### 自然语言风格

```
帮我把 document.docx 加上 AIGC 标记
为 report.pdf 添加 AI 生成标识
给 data.xlsx 文件打上 AIGC 标签
为 presentation.pptx 添加 AIGC 标识
给 notes.md 文件添加 AIGC 标识
```

## 输出说明

- 文件会被直接修改，添加 AIGC 元数据
- 原始文件内容保持不变
- 添加的 AIGC 隐式标记不影响文档正常显示
- 显式标记默认添加在文档末尾、PDF 每页底部，或图片右下角
- 图片显式标记使用"AI生成"文字，带半透明背景条，字号不低于画面最短边的 5%
- 音频显式标记为末尾追加 "AI" 摩斯码节奏（"短长 短短"）
- 视频显式水印位于画面右下角，全程显示，字号为画面最短边的 5%（下限 10 像素），满足国标对显式标识的要求
- 视频显式水印通过 ffmpeg drawtext 重编码生成（MP4/MKV/FLV 用 libopenh264，AVI 用 mpeg4），音轨保持原码流复制
- 使用 `--skip-visible` 参数可仅添加隐式元数据（音频跳过摩斯码；视频跳过 drawtext 重编码，更快）
