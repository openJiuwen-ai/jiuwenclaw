# PptxGenJS PPT 生成指南

## 生成模式

**优先使用 JSON 模板模式**：输出结构化 JSON 数据，由 `scripts/render_ppt.js` 渲染为 PPT。
仅在用户要求完全自定义设计、或现有 layout 无法满足需求时，才降级为手写完整 JS 代码。

```
JSON 模板模式（默认）：输出 slides.json → node scripts/render_ppt.js slides.json output.pptx
自定义代码模式（降级）：输出完整 JS 脚本 → node generate.js → output.pptx
```

---

## 一、JSON 模板模式

### ⚠️ JSON 生成约束（强制执行）

LLM 生成 JSON 时必须严格遵守以下规则，违反任意一条都会导致 JSON 解析失败：

#### 1. 引号规范

| 场景 | 禁止写法 | 正确写法 |
|------|---------|---------|
| 中文引用 | `"被称为"死亡走廊""` | `"被称为「死亡走廊」"` |
| 中文书名 | `"推出"问界M9"车型"` | `"推出《问界M9》车型"` |
| 英文引用 | `"called "The Best""` | `"called 'The Best'"` |
| 强调词 | `"所谓"智能化""` | `"所谓「智能化」"` |

**核心规则**：JSON 字符串值内部禁止出现未转义的 ASCII 双引号 `"`。中文引号一律使用直角引号「」或书名号《》，英文引号使用单引号 `'`。

#### 2. 特殊字符转义

| 字符 | 场景 | 正确写法 |
|------|------|---------|
| 反斜杠 `\` | 文件路径 `C:\Users\doc` | `"C:\\Users\\doc"` |
| 换行符 | 多行文本 | `"第一行\\n第二行"` |
| 制表符 | 对齐文本 | `"列1\\t列2"` |

#### 3. 值类型约束

- 所有颜色值为纯 6 位 hex 字符串，**不带 `#` 前缀**：`"1E2761"` 而非 `"#1E2761"`
- 数组末尾元素后**不加逗号**（trailing comma）
- 数值字段（如 `columns`）使用数字类型，不用字符串：`"columns": 3` 而非 `"columns": "3"`

### Slides JSON Schema

```jsonc
{
  "meta": {
    "title": "演示文稿标题",
    "author": "作者",
    "theme": "business",       // 主题 key，自动套用内置配色 (详见下方主题表)
    "palette": {               // 可选，覆盖 theme 的个别颜色
      // "primary": "1E2761",
      // "accent": "F96167"
    },
    "fonts": {                 // 可选，覆盖 theme 的默认字体
      "title": "Arial Black",
      "body": "Arial"
    }
  },
  "slides": [
    // 每页一个对象，layout 字段指定类型
  ]
}
```

### 内置主题（`meta.theme`）

指定 `theme` 即可自动套用完整配色与字体，无需手写 palette。`meta.palette` 和 `meta.fonts` 可选择性覆盖个别值。

每个主题包含 6 个基础色：primary（封面/章节背景）、secondary（装饰/卡片底）、accent（图标/强调线/高亮数字）、bg（内容页背景）、textDark（深色文字）、textLight（浅色文字）。

| key | 风格 | primary | secondary | accent | bg | textDark | textLight | 字体 |
|-----|------|---------|-----------|--------|-----|----------|-----------|------|
| `academic` | 学术 | 2C3E50 | D5DBDB | E67E22 | F8F9F9 | 2C3E50 | FFFFFF | Georgia |
| `business` | 商务 | 1E2761 | CADCFC | F96167 | F5F7FA | 1E2761 | FFFFFF | Arial Black |
| `tech` | 科技 | 065A82 | 1C7293 | 02C39A | F0F8FF | 065A82 | FFFFFF | Arial Black |
| `ink` | 国风 | 2C3E50 | D4C5A9 | B84040 | FAF8F5 | 2C3E50 | FFFFFF | Georgia |
| `politics` | 党政 | 8B0000 | F5E6CC | DAA520 | FFF8F0 | 8B0000 | FFFFFF | Arial Black |
| `minimal` | 简约 | 36454F | F2F2F2 | 212121 | FAFAFA | 36454F | FFFFFF | Trebuchet MS |

> 未指定 `theme` 且未提供完整 `palette` 时，默认回退为 `business` 主题。

### 自动视觉特性
- **9种装饰风格自动轮换**：cornerAccent / topDualLine / bottomFade / sideStripe / cornerDots / diagonalCorner / topBracket / waveBottom / subtleFrame，每页不重复
- **双层图标效果**：所有图标圆圈带半透明外圈，增加立体感；图标占圆圈 68%（iconScale 0.68），视觉饱满不空洞
- **图标性能优化**：高频图标预渲染 + 运行时缓存（iconName+color 为 key）+ 同页图标并行渲染（Promise.all），复用图标零额外耗时
- **图标降级机制**：sharp 模块不可用时，自动降级为纯色圆圈（不含图标图像），PPT 正常生成不中断；单个图标渲染失败时跳过该图标，不影响其余元素
- **色条圆角化**：顶部色条、分隔线等使用圆角矩形，更精致
- **丰富色彩派生**：卡片、时间线节点等自动从 palette 派生8种不同色，避免单调
- **柔和行背景**：numbered_list 使用极淡的彩色行背景，不再是单调灰白交替
- **智能字号缩放**：文字超长时自动缩小字号，最小不低于 8pt；若 8pt 仍溢出，截断并追加「…」省略标记
- **深色页装饰升级**：封面/章节/结束页包含多个不同大小的装饰圆，更有层次感
- **内容背景多样化**：6种背景变体自动轮换（大圆/双圆/顶部条带等），避免纯白单调
- **卡片布局双模式**：默认垂直布局（图标在上）+ 水平布局（图标在左），增加排版多样性
- **统一卡片风格**：所有统计卡片统一白底 + 彩色底部色条 + 彩色图标圆 + 白色图标，视觉一致

### ⚠️ 内容长度约束

**PPT 是视觉媒介，文字越短越好。** 渲染引擎对超长文本有三级处理机制：

| 级别 | 触发条件 | 处理方式 |
|------|---------|---------|
| **正常** | 字数在建议范围内 | 按默认字号渲染 |
| **缩放** | 超出建议字数但缩放后 ≥ 8pt | 自动缩小字号至内容可完整显示 |
| **截断** | 缩放至 8pt 仍溢出 | 截断文本并追加「…」省略标记 |

**建议字数上限（超出会触发缩放或截断）：**

| 字段 | 建议上限 | 硬性上限（超出将截断） | 说明 |
|------|---------|---------------------|------|
| `body` (text_image) | 80字 | 150字 | 2-3句话概括 |
| `bullets[]` 每项 | 25字 | 50字 | 短语式要点 |
| `cards[].desc` | 35字（4列≤25字） | 60字 | 一句话描述 |
| `stats[].label` | 12字 | 20字 | 指标名称 |
| `timeline[].desc` | 30字（5节点以上≤20字） | 50字 | 一句话 |
| `comparison rows` | 18字/格 | 30字/格 | 简短对比值 |
| `quote.body` | 60字 | 100字 | 精炼引语 |
| `big_number.label` | 30字 | 50字 | 指标说明 |
| `process steps[].desc` | 50字 | 80字 | 步骤简述 |
| `numbered_list items[].title` | 25字 | 40字 | 条目标题 |
| `numbered_list items[].desc` | 35字 | 60字 | 条目描述 |
| `icon_list items[].desc` | 30字（5项以上≤20字） | 50字 | 功能描述 |

**LLM 生成时应严格控制在「建议上限」以内。** 如果内容确实无法精简，宁可拆分为多页，也不要在单页堆砌过多文字。

### Layout 类型

#### `cover` — 封面页
深色背景 + 大标题 + 副标题 + 脚注。多层装饰圆（右上大圆、左下中圆、右中小圆）+ 底部渐变 accent 带 + 标题下方圆角分隔线。
```json
{ "layout": "cover", "title": "主标题", "subtitle": "副标题", "footnote": "底部说明" }
```

#### `toc` — 目录页
双列网格卡片式目录。每项为柔和彩色背景卡片，内含双层彩色编号圆 + 标题 + 副标题。支持 `·` 分隔符自动拆分标题/副标题分行显示，颜色自动轮换。最多8项，末行单数时自动居中。
```json
{ "layout": "toc", "title": "目录", "items": ["第一章 · 概述", "第二章 · 详解"] }
```

#### `section` — 章节分隔页
深色背景 + 巨大半透明水印编号 + 章节标题 + 左侧圆角 accent 竖线 + 多个装饰圆 + 底部 accent 线。
```json
{ "layout": "section", "number": "01", "title": "章节标题", "subtitle": "章节描述" }
```

#### `text_image` — 图文页
一侧文字（标题 + 正文 + accent 圆点列表），另一侧图片区域。文字区左侧圆角 accent 竖线，图片区三层装饰边框（外层淡色框 + 偏移阴影层 + 主占位区）+ 图标与上下装饰线。
`imagePosition`: `"right"`（默认）或 `"left"`。`imageIcon`：图片区图标名，默认 `"FaImage"`。
```json
{
  "layout": "text_image",
  "title": "页面标题",
  "body": "简短正文",
  "bullets": ["要点一", "要点二", "要点三"],
  "imagePlaceholder": "图片描述",
  "imagePosition": "right",
  "imageIcon": "FaImage",
  "src": "C:\\Users\\me\\Pictures\\photo.png"
}
```

#### `cards` — 卡片网格
2-4 张卡片，每张包含圆角顶部色条 + 双层图标圆圈（白色图标）+ 标题 + 描述。不同卡片图标背景色自动轮换。
支持 `cardStyle`: `"default"`（图标在上，适合短内容）或 `"horizontal"`（图标在左，适合较长描述，仅 ≤3列时生效）。

```json
{
  "layout": "cards",
  "title": "页面标题",
  "columns": 3,
  "cardStyle": "horizontal",
  "cards": [
    { "icon": "FaRocket", "title": "卡片标题", "desc": "一句话描述" }
  ]
}
```

#### `stats` — 大数字统计
2-4 个指标卡片，白色背景 + 底部圆角彩色条（每张不同色）+ 大数字突出显示 + 双层图标圆（白色图标）。每张卡片底部可选配图区（`imagePlaceholder` + `imageIcon`）。

```json
{
  "layout": "stats",
  "title": "页面标题",
  "stats": [
    { "value": "1,200+", "label": "指标名称", "icon": "FaChartBar", "trend": "up", "imagePlaceholder": "配图描述", "imageIcon": "FaImage" }
  ]
}
```

#### `comparison` — 对比表
左右两列对比，圆角深色表头，交替行背景色，每行左侧 accent 小色条。
```json
{
  "layout": "comparison",
  "title": "页面标题",
  "leftLabel": "方案A",
  "rightLabel": "方案B",
  "rows": [
    { "dimension": "维度", "left": "A的值", "right": "B的值" }
  ]
}
```

#### `timeline` — 时间线/流程
横向时间轴。**≤4 个节点**：卡片式上下交替排布，双层节点圆，每个节点不同色。
**≥5 个节点**：紧凑内联模式，彩色节点。
```json
{
  "layout": "timeline",
  "title": "页面标题",
  "steps": [
    { "time": "2022", "title": "阶段标题", "desc": "简短描述" }
  ]
}
```

#### `chart` — 图表页
`chartType`: `"bar"` | `"line"` | `"pie"` | `"doughnut"` | `"scatter"`。
```json
{
  "layout": "chart",
  "title": "页面标题",
  "chartType": "bar",
  "data": {
    "labels": ["Q1", "Q2", "Q3", "Q4"],
    "series": [{ "name": "系列1", "values": [100, 200, 300, 400] }]
  },
  "note": "数据来源说明"
}
```

#### `quote` — 引用页
深色背景 + 大半透明引号装饰 + 引文 + 出处 + 多层装饰圆。适合展示核心理念、名言或品牌口号。
```json
{
  "layout": "quote",
  "body": "智慧重塑豪华，科技定义出行。",
  "author": "—— AITO 问界品牌理念"
}
```

#### `big_number` — 单数字强调页
浅色背景 + 超大数字（自动缩放）+ 标签 + 描述 + 多个装饰圆。适合突出单个关键指标。
```json
{
  "layout": "big_number",
  "title": "关键指标",
  "value": "34.48亿",
  "label": "美元 · Brand Finance 2026 品牌价值",
  "desc": "中国豪华品牌价值第一"
}
```

#### `process` — 垂直流程页
双层编号圆 + 纵向连接线 + 带左侧色条的步骤卡片。首个节点 accent 色圆圈突出起始点。
```json
{
  "layout": "process",
  "title": "发展历程/操作流程",
  "steps": [
    { "title": "步骤一标题", "desc": "简短步骤描述" },
    { "title": "步骤二标题", "desc": "简短步骤描述" }
  ]
}
```

#### `numbered_list` — 编号列表页
柔和彩色行背景 + 左侧圆角竖条 + 编号圆圈 + 标题 + 描述。每行颜色从 palette 派生，视觉丰富。超长描述自动缩字。
```json
{
  "layout": "numbered_list",
  "title": "核心优势",
  "items": [
    { "title": "全栈智能技术", "desc": "华为乾崑ADS 5 + 鸿蒙座舱6" },
    { "title": "完整产品矩阵", "desc": "五大SUV系列，覆盖20-60万" }
  ]
}
```

#### `two_column` — 双栏内容页
左右两栏独立内容，中间优雅分隔线。每栏可包含子标题（浅色标签背景）、正文、bullet 列表。适合对比说明、双主题并列。
```json
{
  "layout": "two_column",
  "title": "页面标题",
  "left": {
    "subtitle": "左栏标题",
    "body": "左栏正文",
    "bullets": ["要点A", "要点B"]
  },
  "right": {
    "subtitle": "右栏标题",
    "body": "右栏正文",
    "bullets": ["要点C", "要点D"]
  }
}
```

#### `icon_list` — 图标列表页
每行一个彩色双层图标圆 + 标题 + 描述，白色卡片背景 + 柔和阴影。适合功能特性、服务项目、团队角色等展示。最多6项，5项以上自动缩小行高并压缩间距，文字超长自动缩字。
```json
{
  "layout": "icon_list",
  "title": "服务项目",
  "items": [
    { "icon": "FaRocket", "title": "快速部署", "desc": "一键上线，极速交付" },
    { "icon": "FaShieldAlt", "title": "安全保障", "desc": "企业级加密与合规" }
  ]
}
```

#### `highlight_box` — 重点框页
浅色背景上的深色大卡片，左侧 accent 竖线 + 可选图标 + 标题 + 正文 + 底部标注。右侧可选配图区（`imagePlaceholder` + `imageIcon`）。适合展示核心结论、重要声明、产品定位等。
```json
{
  "layout": "highlight_box",
  "title": "页面标题",
  "icon": "FaLightbulb",
  "heading": "核心结论",
  "body": "详细说明文字，支持多行展示。",
  "footnote": "数据来源：XXX",
  "imagePlaceholder": "配图描述",
  "imageIcon": "FaImage"
}
```

#### `image` — 图片页
图片占据页面主体，无标题时全页铺开（仅留 0.4" 边距），有标题时显示小标题栏并下移图片区。支持两种模式：
- **本地图片**：指定 `src` 为本地文件路径，直接嵌入图片（白底卡片 + 阴影），`contain` 模式保持原始比例不拉伸
- **占位模式**：不指定 `src` 时显示占位图标 + 描述文字

```json
{
  "layout": "image",
  "title": "页面标题（可选，无标题时图片铺满全页）",
  "src": "C:/Users/me/Pictures/dashboard.png",
  "caption": "图注：数据来源说明（可选）"
}
```

`title`、`caption` 均为可选。`src` 为空时自动降级为占位模式。

#### `ending` — 结束页
深色背景 + 感谢语 + 圆角 accent 分隔线 + 副标题 + 联系方式 + 多层装饰圆 + 顶底 accent 线。
```json
{ "layout": "ending", "title": "感谢观看", "subtitle": "THANK YOU", "contact": "联系方式" }
```

### layout 选用建议

| 场景                    | 推荐 layout | 优先级 |
|-----------------------|------------|-------|
| 概念+图解说明               | `text_image` | ⭐⭐⭐ 优先使用 |
| 分类要点（产品/方案/优势）        | `cards` | ⭐⭐⭐ |
| 要点罗列/优势特征/清单列表（4-7条）  | `numbered_list` | ⭐⭐ |
| 双主题并列/优劣对比            | `two_column` | ⭐⭐ |
| 核心理念/品牌口号             | `quote` | ⭐⭐ |
| 方案对比/竞品分析             | `comparison` | ⭐ |
| 功能特性 + 图标展示（3-5项）     | `icon_list` | ⭐ |
| 操作流程/发展历程/步骤说明（4-6步）  | `process` | ⭐ |
| 历史沿革/里程碑              | `timeline` | ⭐ |
| 图片展示（产品图/架构图/实景照片/绘本） | `image` | ⭐ |
| 核心结论/重要声明             | `highlight_box` | ⭐ |
| 关键数据对比（多项指标）          | `stats` | 谨慎使用 |
| 单个关键指标（收入/用户数/增长率）    | `big_number` | 谨慎使用 |

### ⚠️ 页面类型组成规范（强制执行）

**核心原则：图文页优先，数据页克制。** PPT 是视觉媒介，文字+配图比纯数据卡片更有阅读吸引力。

| 页面类型 | 占比要求 | 说明 |
|----------|---------|------|
| **图文页**（text_image / two_column） | **≥25% 内容页** | 每 2-3 个其他页面必须穿插 1 个图文页 |
| **纯数据页**（stats / big_number） | **≤15% 内容页，且合计 ≤3 页** | 数据用 cards / numbered_list 替代更佳 |
| **内容卡片**（cards / numbered_list） | 建议 25-35% 内容页 | 作为主体信息承载 |
| **流程/时间线**（process / timeline） | 合计 ≤2 页 | 仅关键流程使用 |

**规则与优先级（冲突时按序号优先）：**

1. **P0 — 相邻不重复**：相邻页面禁止使用相同 layout（最高优先级，无例外）
2. **P1 — 数据页限制**：禁止连续出现 2 个 `stats` 或 `big_number` 页面；`stats` 页优先改用 `cards` + `cardStyle: "horizontal"` 替代
3. **P2 — 图文穿插**：每个章节（section 到下一个 section 之间）至少包含 1 个 `text_image` 图文页
4. **P3 — 数据融入**：优先将数据融入 `text_image`（文字+配图）或 `numbered_list`（编号列表），而非独立展示

**短章节例外（P2 放宽条件）**：
- 章节内容页仅 1-2 页时，P2 不强制要求 `text_image`，但应优先选择 `text_image` 或 `two_column` 作为该章节的 layout
- 纯数据分析章节（如财报数据、性能指标）允许不含 `text_image`，但必须满足 P0 和 P1

**设计节奏**：
- **图文优先**：先确定 text_image 的位置（每章 1 个），再填入其他页面类型
- **交替搭配**：相邻页面必须使用不同 layout，避免连续出现同类型
- **数据克制**：stats/big_number 仅在「必须用大数字制造冲击感」时使用，普通数据用 cards/numbered_list 承载
- **卡片变体**：同一 layout 重复出现时，交替使用 `cardStyle: "default"` / `"horizontal"` 或不同列数增加变化
- **章节过渡**：`section` 页后紧跟的页面优先使用 text_image 作为章节开场
- **封面/结尾呼应**：`cover` 与 `ending` 使用相同配色，首尾呼应形成闭环

### 图标名称

icon 字段使用 react-icons 组件名（Fa6 风格）。渲染引擎会自动在 Fa/Md/Hi/Bi 四个图标库中查找。

| 类别 | 图标名 |
|------|--------|
| 通用 | FaRocket, FaLightbulb, FaStar, FaHeart, FaFlag |
| 数据 | FaChartLine, FaChartBar, FaChartPie, FaDatabase |
| 业务 | FaUsers, FaBuilding, FaBriefcase, FaHandshake |
| 状态 | FaCheckCircle, FaShieldAlt, FaTrophy, FaBullseye |
| 技术 | FaCog, FaCode, FaMicrochip, FaWifi, FaBolt |
| 交通 | FaGlobe, FaMapMarkerAlt, FaTruck, FaCar, FaPlane, FaShip |
| 自然 | FaLeaf, FaTree, FaSun, FaMoon, FaCloud, FaImage |
| 媒体 | FaCamera, FaVideo, FaMusic, FaHeadphones, FaPlay |
| 财务 | FaMoneyBill, FaDollarSign, FaCoins, FaWallet |
| 教育 | FaBook, FaGraduationCap, FaPencilAlt, FaSchool |
| 医疗 | FaHeartbeat, FaMedkit, FaStethoscope, FaHospital |
| 电商 | FaShoppingCart, FaStore, FaTag, FaGift |
| 时间 | FaClock, FaCalendar, FaHourglass, FaHistory |
| 通信 | FaEnvelope, FaPhone, FaComment, FaBell |
| 安全 | FaLock, FaKey, FaFingerprint, FaUserSecret |
| 建筑 | FaHome, FaCity, FaWarehouse, FaIndustry |

### 图标使用规范

1. **语义匹配**：图标含义与内容对应（增长率→FaChartLine，安全→FaShieldAlt，团队→FaUsers）
2. **同一页内不重复**：每个卡片/列表项使用不同图标，丰富视觉层次
3. **风格统一**：同一演示中优先使用 Fa 系列，保持视觉一致性
4. **跨页复用 OK**：不同页面可以复用图标（视觉独立）
5. **可选原则**：icon 字段为可选，未指定时不绘制图标圆圈，不出现空白圆圈占位——只有需要图标语义强化时才添加
6. **色彩层级**：图标圆圈颜色从 palette 自动派生，每张卡/每行自动轮换不同颜色，避免单调
7. **优选建议**：核心指标用 FaTrophy/FaChartLine/FaStar，流程用 FaCog/FaBolt/FaRocket，团队用 FaUsers/FaHandshake，文化用 FaBook/FaHistory/FaStar

### 颜色层级体系

渲染引擎从 6 个基础色自动派生 8 个卡片色 + 8 个背景色，形成丰富的视觉层级：

| 层级 | 来源 | 用途 |
|------|------|------|
| **基础色 (6)** | `meta.palette` | primary（封面/章节背景）、secondary（装饰/卡片底）、accent（图标圆圈/强调线/高亮数字）、bg（内容页背景）、textDark/textLight/textMuted（文字） |
| **派生色 (8)** | `deriveCardColors(p)` | 从 accent、primary 混合/明度变化生成 8 种不同色，用于卡片图标圆、统计底条、编号圆、时间线节点等——自动轮换避免重复 |
| **背景色 (8)** | `deriveCardBgColors(p)` | 从 accent、primary 极淡派生 + 预设柔和色（F0F9FF/FFF7ED/F0FDF4等），用于 numbered_list 行背景、TOC 卡片背景等 |
| **装饰色** | lighten/darken/mixHex | 9 种装饰风格自动轮换（cornerAccent/topDualLine/bottomFade等），每页不重复，均从 accent 派生 |

**关键设计**：AI 生成 JSON 时只需指定 6 个基础色，渲染引擎自动完成所有派生和轮换，确保视觉丰富且一致。

### 领域图标推荐

| 主题 | 推荐图标 |
|------|---------|
| 科技/AI | FaMicrochip, FaCode, FaBolt, FaWifi, FaRocket |
| 汽车/制造 | FaCar, FaTruck, FaIndustry, FaCog, FaBolt |
| 金融/财经 | FaChartLine, FaChartBar, FaDollarSign, FaDatabase |
| 医疗/健康 | FaHeartbeat, FaMedkit, FaStethoscope, FaHospital |
| 文旅/自然 | FaLeaf, FaTree, FaMapMarkerAlt, FaImage, FaSun |
| 教育/学术 | FaBook, FaGraduationCap, FaPencilAlt, FaSchool |
| 政企/党建 | FaBuilding, FaFlag, FaHandshake, FaBullseye |
| 零售/消费 | FaShoppingCart, FaStore, FaTag, FaGift, FaStar |

---

## 二、渲染引擎

渲染引擎为预置脚本 `scripts/render_ppt.js`，首次运行自动安装依赖。

### 使用

```bash
node scripts/render_ppt.js slides.json output.pptx
```

### 依赖与降级策略

渲染引擎依赖以下 npm 包：

| 包名 | 用途 | 是否必需 | 不可用时的降级行为 |
|------|------|---------|------------------|
| `pptxgenjs` | PPT 文件生成 | **必需** | 无法生成，报错退出 |
| `react` | 图标组件解析 | **必需** | 无法生成，报错退出 |
| `react-dom` | 图标 SVG 渲染 | **必需** | 无法生成，报错退出 |
| `react-icons` | 图标库 | 可选 | 所有图标圆圈退化为纯色圆（无图标图像），PPT 正常生成 |
| `sharp` | 图标 SVG→PNG 光栅化 | 可选 | 同上，图标圆圈退化为纯色圆，控制台输出 warning |

**降级层级**：
1. **完整模式**：所有依赖可用 → 双层图标圆 + SVG 图标渲染
2. **无图标模式**：sharp 或 react-icons 不可用 → 纯色圆圈占位（accent 色填充，无图标图像），不影响文字/布局/配色
3. **失败退出**：pptxgenjs / react / react-dom 缺失 → 报错并提示安装命令

单个图标渲染失败（如图标名拼写错误）时，跳过该图标，在控制台输出 `[WARN] Icon not found: FaXxx, fallback to plain circle`，不中断整体流程。

### 扩展自定义 Layout

编辑 `scripts/render_ppt.js`，在 `renderers` 对象中添加新函数：

```javascript
renderers.my_layout = async function(slide, data, p, pres) {
  slide.background = { color: p.bg };
  // 自由使用 pptxgenjs API
};
```

JSON 中对应使用 `"layout": "my_layout"`。

---

## 三、设计规范

### 配色方案

选择与主题匹配的配色，不要默认蓝色。

| 风格 | primary | secondary | accent |
|------|---------|-----------|--------|
| 深蓝商务 | `1E2761` | `CADCFC` | `F96167` |
| 科技青绿 | `065A82` | `1C7293` | `02C39A` |
| 暖橙活力 | `B85042` | `E7E8D1` | `A7BEAE` |
| 森林自然 | `2C5F2D` | `97BC62` | `F5F5F5` |
| 炭灰极简 | `36454F` | `F2F2F2` | `212121` |
| 莓红典雅 | `6D2E46` | `A26769` | `ECE2D0` |
| 海洋渐深 | `065A82` | `1C7293` | `21295C` |
| 珊瑚活力 | `F96167` | `F9E795` | `2F3C7E` |

### 字体搭配

| title | body | 适用场景 |
|-------|------|---------|
| Arial Black | Arial | 通用商务 |
| Georgia | Calibri | 文艺高端 |
| Impact | Arial | 强视觉冲击 |
| Trebuchet MS | Calibri | 现代简约 |
| Cambria | Calibri | 学术稳重 |

### 设计原则

- **色彩层级**：6 基础色 → 8 派生色 → 8 背景色，全自动派生，AI 只需指定 palette
- **图标审美**：双层圆圈（半透明外圈 + 实色内圈），icon 为空时不绘制圆圈，不出现空白占位
- **装饰轮换**：9 种装饰风格自动轮换，每页不重复，均从 accent 派生
- **背景呼吸**：6 种内容背景变体自动轮换（大圆/双圆/顶部条带等），避免纯白单调
- **布局节奏**：相邻页面不同 layout，图文穿插呼吸，数据页用 section/quote 过渡
- 深色背景用于封面、章节页、引用页、结束页；浅色用于内容页
- 每页必须有视觉元素（图标、图表、形状、装饰圆），避免纯文字
- 安全边距 0.5"，元素间距 0.3"
- 强对比：深色背景用浅色文字，浅色背景用深色文字
- 文字超长时自动缩小字号（`fitText`），最小 8pt；8pt 仍溢出则截断追加「…」

---

## 四、PptxGenJS API 速查

仅在自定义代码模式或扩展 layout 时参考。

### 坐标系
- LAYOUT_16x9: 10" × 5.625"（单位：英寸）
- 原点左上角，x 向右，y 向下

### 核心 API

```javascript
// 文字
slide.addText("text", { x, y, w, h, fontSize, fontFace, color, bold, align, valign, margin });

// 多行文字
slide.addText([
  { text: "行1", options: { breakLine: true } },
  { text: "行2" }
], { x, y, w, h });

// 列表
slide.addText([
  { text: "项1", options: { bullet: true, breakLine: true } },
  { text: "项2", options: { bullet: true } }
], { x, y, w, h });

// 形状: RECTANGLE, OVAL, LINE, ROUNDED_RECTANGLE
slide.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color }, shadow: {...} });

// 图片
slide.addImage({ path: "url_or_path", x, y, w, h });
slide.addImage({ data: "image/png;base64,...", x, y, w, h });

// 图表: BAR, LINE, PIE, DOUGHNUT, SCATTER
slide.addChart(pres.charts.BAR, [{ name, labels, values }], { x, y, w, h, barDir: "col" });

// 背景
slide.background = { color: "F1F1F1" };
```

### 阴影

```javascript
{
  shadow: {
    type: "outer",     // "outer" | "inner"
    color: "000000",   // 6位 hex，无 #
    blur: 6,           // 0-100
    offset: 2,         // 必须 ≥ 0
    angle: 135,        // 0-359（135=右下，270=上方）
    opacity: 0.15      // 0.0-1.0，不要编码到 color 里
  }
}
```

### 致命错误清单（PptxGenJS API）

| 写法 | 后果 | 正确写法 |
|------|------|---------|
| `color: "#FF0000"` | 文件损坏 | `color: "FF0000"` |
| `color: "00000020"` | 文件损坏 | `color: "000000", opacity: 0.12` |
| `offset: -5` | 文件损坏 | `offset: 5, angle: 270` |
| `"• 项目"` | 双重圆点 | `{ bullet: true }` |
| 复用 options 对象 | 第二个形状异常 | 每次新建或工厂函数 |
| `lineSpacing` + bullets | 间距过大 | 用 `paraSpaceAfter` |
| 不支持渐变填充 | 无效果 | 用渐变图片做背景 |

---

## 五、常见坑点与排查

### ⚠️ JSON 生成常见错误

#### 1. 中文引号导致 JSON 解析失败（最常见）

**现象**：`node scripts/render_ppt.js slides.json output.pptx` 直接报 `SyntaxError: Expected ',' or '}' after property value`

**原因**：LLM 生成的 JSON 中，中文字段值内使用了 ASCII `"` 作为中文引号（如 `"被称为"死亡走廊""`），与 JSON 字符串分隔符冲突。

```
错误: "desc": "曾被称为"死亡走廊""
正确: "desc": "曾被称为「死亡走廊」"
```

**防御措施**（多层防护）：
1. **生成端约束**（首选）：LLM system prompt 中强制要求 JSON 值内中文引号使用「」，英文引号使用 ''，详见本文档「JSON 生成约束」一节
2. **sanitizeJson() 自动修复**（兜底）：render_ppt.js 内置预处理函数，尝试将值内的中文引号替换为直角引号。覆盖场景有限，无法处理所有边界情况
3. **手动修复**（最后手段）：如 sanitizeJson() 仍报错，手动将值内引号替换为「」或 Unicode 引号""''

#### 2. 反斜杠未转义

**现象**：JSON 解析报 `SyntaxError: Bad escaped character` 或 `Unexpected token`

**原因**：文件路径、公式等内容包含 `\`，在 JSON 字符串中未转义。

```
错误: "desc": "路径为 C:\Users\doc\report.pdf"
正确: "desc": "路径为 C:\\Users\\doc\\report.pdf"
```

**修复**：LLM 生成时对所有 `\` 转义为 `\\`。render_ppt.js 的 sanitizeJson() 也会尝试自动修复常见的 Windows 路径模式。

#### 3. 颜色值带 `#` 前缀

**现象**：生成的 PPTX 文件无法打开或形状颜色异常。

```
错误: "primary": "#1E2761"
正确: "primary": "1E2761"
```

PptxGenJS 的 `color` 字段不接受 `#` 前缀，palette 中所有颜色值必须是纯 6 位 hex。

#### 4. 超大 JSON 导致 Node.js 内存溢出

**现象**：`JavaScript heap out of memory`

**修复**：减少 slides 数量（建议 ≤20 页），或增大 Node.js 内存限制：
```bash
node --max-old-space-size=4096 scripts/render_ppt.js slides.json output.pptx
```

### 🔧 依赖安装问题

#### npm 全局安装权限错误

**现象**：`npm error permission denied` 在 Linux/Mac 环境安装依赖时。

**修复**：render_ppt.js 已内置 `checkDeps()` 自动在脚本目录本地安装依赖（`npm install --no-save`），无需手动 `npm install -g`。如手动安装报权限错误，使用：
```bash
npm install --no-save pptxgenjs react react-dom react-icons sharp
```

#### sharp 安装失败（Linux sandbox）

**现象**：`npm install sharp` 编译失败或缺少 libvips。

**修复**：使用预编译版本或跳过可选依赖：
```bash
npm install --no-save --ignore-scripts sharp && npm install --no-save pptxgenjs react react-dom react-icons
```

**注意**：sharp 安装失败不阻断 PPT 生成。渲染引擎会自动降级为「无图标模式」（纯色圆圈，无图标图像），控制台输出降级提示。

### 📐 布局与内容常见问题

#### 卡片文字溢出

**现象**：卡片中文字极小，几乎不可读。

**原因**：4 列卡片时 `desc` 建议 ≤25 字，3 列时 ≤35 字。超出时渲染引擎自动缩字，最小到 8pt；8pt 仍溢出则截断加「…」。应在 LLM 生成端控制字数。

#### 图标不显示

**现象**：卡片/stats 中图标区域为纯色圆圈，无图标图像。

**排查**：
1. 检查 sharp 模块是否安装成功（`node -e "require('sharp')"`）——安装失败时自动降级为纯色圆
2. 检查 icon 名称是否在 react-icons 中存在（优先使用 Fa 系列）——名称错误时该图标降级为纯色圆，控制台输出 `[WARN]`
3. 图标名称大小写敏感（如 `FaChartLine` 不是 `FaChartline`）

#### TOC 项目过多

**现象**：目录页卡片过于拥挤。

**限制**：TOC 最多 8 项。超出部分不渲染。建议 4-8 项，使用 `·` 分隔符拆分主标题/副标题。

#### 相邻页面重复布局

**问题**：连续多页使用相同 layout 导致视觉单调。

**建议**：相邻页面交替使用不同 layout（如 cards → stats → timeline → text_image），利用设计节奏保持观众注意力。