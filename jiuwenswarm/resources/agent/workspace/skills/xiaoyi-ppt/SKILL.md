---
name: xiaoyi-ppt
description: >
  PPT/幻灯片综合处理技能，支持演示文稿的从零生成与现有文件的直接编辑。
  适用情形：
  1. 新建 PPT：根据给定的主题、上传的文档或网络检索内容，规划大纲并生成完整幻灯片；
  2. 编辑 PPT：对已有的 PPTX 文件进行修改，包括调整排版、修改文本、增删与重排页面；
  3. 模板套用：基于用户提供的模板 PPT 文件填入新内容。
  只要用户意图涉及“做PPT”、“生成幻灯片”、“修改演示文稿”或“排版PPT”时必须触发。
metadata:
  openclaw:
    requires:
      bins:
        - python3
---

# PPT 生成&编辑 Skill

信息整理（文档解析 / 网络搜索）+ 结构化大纲生成 + 云端 PPT 生成的完整流程；也支持对已有 PPTX 文件进行直接编辑修改。

---

## 环境初始化（始终最先执行此步骤）

**此技能需要 Python 3 (>=3.8)。在运行任何脚本之前，执行以下命令定位有效的 Python 可执行文件并安装依赖。**

```bash
PYTHON_CMD=""
for cmd in python3 python python3.13 python3.12 python3.11 python3.10 python3.9 python3.8; do
  if command -v "$cmd" &>/dev/null && "$cmd" -c "import sys; exit(0 if sys.version_info >= (3,8) else 1)" 2>/dev/null; then
    PYTHON_CMD="$cmd"
    break
  fi
done

if [ -z "$PYTHON_CMD" ]; then
  echo "错误：未找到 Python 3.8+"
  exit 1
fi

echo "已找到 Python：$PYTHON_CMD ($($PYTHON_CMD --version))"

$PYTHON_CMD -m pip install -q --break-system-packages requests
echo "依赖已就绪。"
```

> 检查完成后，在后续所有命令中使用发现的 `$PYTHON_CMD` 替代 `python`。

---

## 会话初始化（环境检查完成后立即执行）

```bash
export PPT_SESSION_ID="${PPT_SESSION_ID:-$(uuidgen 2>/dev/null || $PYTHON_CMD -c 'import uuid; print(uuid.uuid4())')}"
export PPT_SESSION_DIR="/tmp/xiaoyi_ppt/$PPT_SESSION_ID"
mkdir -p "$PPT_SESSION_DIR"
echo "会话 ID：$PPT_SESSION_ID"
echo "会话目录：$PPT_SESSION_DIR"
```

| 变量                                                       | 路径 |
|----------------------------------------------------------|------|
| `~/.openclaw/workspace/skills/xiaoyi-ppt`                | 本 skill 根目录（由运行环境注入） |
| `~/.openclaw/workspace/skills/xiaoyi-ppt/scripts/`       | 脚本目录 |
| `$PPT_SESSION_DIR`                                       | `/tmp/xiaoyi_ppt/$PPT_SESSION_ID/` |
| `/tmp/xiaoyi_ppt/$PPT_SESSION_ID/outline_pre.md`         | 原始大纲文件 |
| `/tmp/xiaoyi_ppt/$PPT_SESSION_ID/outline.md`             | 替换图片后的大纲文件 |
| `/tmp/xiaoyi_ppt/$PPT_SESSION_ID/images/`                | 生成的图片目录 |
| `/tmp/xiaoyi_ppt/$PPT_SESSION_ID/image_urls.json`        | 图片 URL 列表 |
| `/tmp/xiaoyi_ppt/$PPT_SESSION_ID/generate.log`           | 运行日志 |
| `~/.openclaw/workspace/skills/xiaoyi-ppt/edit.md`        | PPT 编辑提示词 |

---

## 任务分流

环境和会话初始化完成后，根据用户意图选择对应流程：

| 用户意图 | 判断依据 | 执行流程 |
|---------|---------|---------|
| **新建 PPT** | 用户要求从零生成 PPT，提供文档或主题 | → 进入「生成流程」（子流程一 ~ 三） |
| **编辑已有 PPT** | 用户提供了 PPTX 文件并要求修改，或要求以某个 PPTX 为模板填入新内容 | → 进入「编辑流程」（步骤 E1 ~ E3） |

---

## 生成流程

按顺序执行以下三个子流程。**每个子流程开始前，必须先完整阅读对应的 MD 文件，再执行任何操作。**

### 子流程一：信息搜索 & 确认写作思路

> **必须先阅读 `~/.openclaw/workspace/skills/xiaoyi-ppt/step1_search_confirm.md`，再执行此子流程。**

覆盖范围：
- 从文档或网络搜索收集信息
- 梳理写作思路并与用户对齐确认

完成标志：用户确认写作思路，输出 `✅ 写作思路已确认`

---

### 子流程二：生成大纲

> **必须先阅读 `~/.openclaw/workspace/skills/xiaoyi-ppt/step2_outline.md`，再执行此子流程。**

覆盖范围：
- 基于已确认的写作思路生成Markdown格式的完整大纲（以<`<style>` 开头，必须遵守step2_outline.md的「步骤三：格式规范」内容，含`<image_user_provided>`、`<image_search_queries>` 和 `<image_gen_queries>` 图片策略标签），以图文并茂为最终目标考虑场景是否需要配图，不强制进行图片引用，按照搜索规范优先使用用户图片，再使用 image_search_queries，最后使用 image_gen_queries。
- 保存大纲到本地文件

完成标志：大纲保存完成，输出 `✅ 大纲生成完成`

**大纲生成完成格式验证要求**：
- 大纲文件必须以 `<style>xxx</style>` 开头，其中 xxx 为具体的风格标识（如 CreamInk、PlatinumExecutive、WarmPaper）
- 该格式要求在大纲生成后、图片处理前进行验证
- 如果格式不正确（如大纲文件`outline_pre.md`为JSON格式、JSON对象等），须重新生成大纲（以Markdown形式）

验证脚本：
```bash
$PYTHON_CMD ~/.openclaw/workspace/skills/xiaoyi-ppt/scripts/validate_outline.py "$PPT_SESSION_DIR/outline_pre.md"
```
如果验证脚本不通过，回到「子流程二：生成大纲」步骤重新生成大纲，保证符合**大纲生成完成格式验证要求**。

大纲中图片策略标签生成规范：
✅ **适合image_search_queries搜索的图片类型**（中文图库能稳定出好图，鼓励挖掘）：

1. **实物/产品图**：具体型号的手机、汽车、家电、工具、食物、饮品、服装、书籍封面等，以及产品的关键零部件（电池、屏幕、激光雷达、座椅、音响、轮毂等，只要是有明确物理形态的东西）
2. **地点/建筑/风景**：地标建筑、自然风光、城市街景、有特征的室内空间
3. **人物图**：知名人物肖像、历史人物、特定职业形象
4. **动植物/自然生物**：具体物种、自然现象
5. **艺术品/文物**：画作、雕塑、瓷器、书法（具体作品或品类）
6. **事件现场**：发布会、比赛、演出、仪式
7. **工艺/制作过程**：有视觉特征的工艺（榫卯、茶艺、锻造、手术操作）
8. **典型行业工作场景**：必须一看就知道是哪个行业的场景，如"呼叫中心坐席""芯片无尘车间""直播主播间"——**不是**"办公室""会议室"这种通用场景

❌ **不适合搜索的内容**（image_gen_queries配图）：

1. **需要自行绘制的数据图表**（柱状图/折线图/饼图/散点图）→ 应由 echarts 绘制。**注意：markdown 表格不属于此类**，带表格的页面如果涉及实物对比，仍然应当配实物图
2. **逻辑图/架构图/流程图/时序图** → 搜出来全是论文截图和PPT截图，毁版面
3. **组织结构图/时间线/思维导图** → 同上，私人定制性太强
4. **抽象概念/算法机制/方法论**（创新、增长、协作、突破、赋能、双模式、计算分配、性能对比）→ 这类内容没有物理对应物。**不要试图用"科技感 / 可视化 / 概念图 / 神经网络"等装饰词凑出 query**——这是放弃信号，不是修复手段。这类页面应当依靠表格、公式、原文图、echarts 来承载信息，不配图是正确选择
5. **通用场景**（办公室、会议、电脑前的人）→ 和内容无特指关系，等于装饰
6. **纯文字/符号类**（代码、公式、文案）→ 无搜索必要
---

#### 子流程二·一：图片生成与替换

> **前置条件**：子流程二已完成，大纲文件保存在 `$PPT_SESSION_DIR/outline_pre.md`。

**目标**：
1. 解析大纲中的 `<image_user_provided>` 标签，将用户上传的图片预处理后上传到 OSMS，替换为实际图片引用。
2. 解析大纲中的 `<image_gen_queries>` 标签，调用 Seedream 生成图片，替换为实际图片引用，并将图片上传获取 URL。
3. `<image_search_queries>` 标签保留不动，由后续流程处理。

```bash
$PYTHON_CMD ~/.openclaw/workspace/skills/xiaoyi-ppt/scripts/replace_gen_images.py "$PPT_SESSION_DIR/outline_pre.md"
```

脚本内部流程：
1. 正则提取大纲中所有 `<image_user_provided>` 标签，预处理用户图片（格式转换），上传到 OSMS，替换为 `![{"caption":"...","content":"","width":"...","height":"...","filename":"xxx.jpg"}](xxx.jpg)` 格式
2. 正则提取大纲中所有 `<image_gen_queries>caption</image_gen_queries>` 标签
3. 逐条调用 Seedream API 生成图片并下载。
4. 将 `<image_gen_queries>` 替换为 `![{"caption":"...","content":"","width":"...","height":"..."}](filename)` 格式
5. 上传所有图片到 OSMS，将 URL 列表保存至`$PPT_SESSION_DIR/image_urls.json`
6. 替换后的大纲保存至`$PPT_SESSION_DIR/outline.md`

**完成后**：
- 替换后的大纲文件：`$PPT_SESSION_DIR/outline.md`
- 图片 URL 列表：`$PPT_SESSION_DIR/image_urls.json`

完成标志：输出 `✅ 图片生成与替换完成`

---

### 子流程三：调用云服务 & 监控 & 交付

> **必须先阅读 `~/.openclaw/workspace/skills/xiaoyi-ppt/step3_generate_monitor.md`，再执行此子流程。**

覆盖范围：
- 使用替换图片后的大纲文件（`outline.md`）调用 `generate_ppt.py`
- 通过 `--image-urls-file` 将图片 URL 列表传入 attachment 参数
- 每 15 秒轮询日志，实时汇报进展（最多 80 次）
- 任务完成后向用户交付文件
- **失败自动重试与降级**：云服务连续失败 ≥2 次后，自动降级到简易模式（PptxGenJS 本地生成），不对用户暴露模式切换

完成标志：PPT 生成完成，输出 `✅ PPT 生成完成！`

---

## 编辑流程

当用户提供已有 PPTX 文件并要求修改，或以某个 PPTX 为模板填入新内容时，进入编辑流程。

### 步骤 E1：读取编辑指导并执行

> **必须先阅读 `~/.openclaw/workspace/skills/xiaoyi-ppt/edit.md`，再执行任何编辑操作。**

edit.md 是编辑流程的**唯一权威参考**，包含完整的操作规范（模板分析、XML 解包/打包、幻灯片增删改序、内容编辑、格式规则、常见陷阱等）。读取后，严格按照其中的步骤和约束执行全部编辑操作。

**每完成一张幻灯片的编辑，向用户汇报进展。**

---

### 步骤 E2：AIGC 水印标记

编辑完成、打包出最终 PPTX 后，**必须调用水印脚本**为文件添加 AIGC 标识：

```bash
$PYTHON_CMD ~/.openclaw/workspace/skills/xiaoyi-ppt/scripts/ppt_aigc_mark.py $PPT_SESSION_DIR/input.pptx
```

脚本自动完成：提取全文 → 生成 AIGC 签名 → 添加首页可见水印 + 隐式 custom property → 覆盖写入原文件。

---

### 步骤 E3：交付

**将标记完成的 `{原文件名}_edited.pptx` 文件发送给用户。**

完成标志：输出 `✅ PPT 编辑完成！`

---

## 依赖

- **Python 3.8+**（必需）— `python3` / `python` 必须在 PATH 中
- **requests 库** — 环境检查步骤自动安装
- **已安装的文档解析 skill** — 当用户提供文档时使用
- **已安装的网络搜索 skill** — 当需要在线搜索信息时使用
- **`~/.openclaw/.xiaoyienv`** — OSMS 服务配置文件，必须包含 `SERVICE_URL`