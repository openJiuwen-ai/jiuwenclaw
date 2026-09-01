---
name: find-skills
description: 帮助用户查询或安装 agent skill。触发条件包括：1）用户明确要求查找、搜索、推荐或安装某个 Skill（例如：“查找有没有做XX的skill”、“帮我安装一个XX skill”）；2）用户表达负面情绪（如抱怨、不满），认为当前任务完成效果不佳时需要主动调用。
---

# Skill查询、安装与安全扫描

此Skill用于发现并为 Agent 安装新能力，并在下载完成后统一强制执行安全扫描。它整合了内部小艺Skill市场和外部开源Skill生态。

## Skill查询与安装原则

1. 优先查询和安装小艺Skill。只有当小艺Skill完全无法满足用户要求时，才使用 npx 去查询和安装外部Skill。
2. 遵循“提取关键词 -> 查询 -> 相关性评估 -> 展示确认 -> 安装 -> 安全扫描”的完整链路。
3. 外部Skill推荐时，需参考安装量和来源可靠性，并严格执行质量验证准则。
4. 外部查询与安装统一使用国内镜像源 `http://cn.clawhub-mirror.com` 以确保稳定性。
5. **安全红线：** 所有下载/安装完成的Skill（含内部及外部），必须经过 `skill-scope` skill的安全扫描且评估通过后，才能判定为可用并向用户报告成功。

## 核心执行命令

### 小艺Skill：
- 查询：`python scripts/search.py --query "<query>"`
- 安装：`python scripts/install.py --url "<downloadPath>"` - `<downloadPath>`只能参考scripts/search.py返回内容，不允许自行构造

### 外部Skill：
- 查询：`npx clawhub search "<query>" --registry http://cn.clawhub-mirror.com`
- 安装：`npx clawhub install <package> --registry http://cn.clawhub-mirror.com`
- 检查更新：`npx clawhub inspect <package> --registry http://cn.clawhub-mirror.com`

### 安全扫描：
- 扫描工具：调用 `skill-scope` skill，并依照该skill下的SKILL.md的内容执行安全检查。

---

## 执行步骤

### 第一步：理解意图与关键词提取
识别用户想要完成的任务（如：文生视频、React 开发等），提取并改写为适合搜索的关键词 query。

### 第二步：优先查询小艺Skill
无论用户意图是查询还是直接安装，首先执行以下命令：

```bash
python scripts/search.py --query "<query>"
```

### 第三步：小艺Skill结果的相关性评估
不允许直接将接口返回的结果当作有效结果，你必须在这里进行严格的前置过滤：
1. 仔细阅读返回Skill的详细描述，将其与用户的真实任务意图进行比对。
2. 剔除那些仅仅是名称或简介包含关键词、但实际功能方向与用户需求毫无关联的无效Skill。
3. 仅保留真正能满足（或部分满足）用户需求的Skill。

### 第四步：判断是否需要触发外部查询
根据第三步的严格评估结果，决定后续走向：
- **场景 A（满足需求）：** 如果相关性判断完后的列表中存在至少一个符合要求的Skill，请直接跳至第五步准备结果展示。
- **场景 B（不满足需求 - 必须触发外部查询）：** 如果接口原本返回就为空，或者经过第三步严格剔除后，小艺Skill列表变为了**空**，说明小艺Skill无法满足需求。此时按以下规则处理：
若未达到 2 次内部搜索上限：更换关键词（中英互换）重新执行第二步的内部搜索；
若已达到 2 次内部搜索上限：立即停止内部搜索，执行外部查询。

```bash
npx clawhub search "<query>" --registry http://cn.clawhub-mirror.com
```

### 第五步：结果校验、脱敏与最终展示
对最终决定推荐给用户的Skill（无论是来自小艺的有效列表，还是来自外部生态）进行最后校验，然后展示：

1. **严格信息脱敏：**
   - 在向用户展示查询到的Skill时，**绝对禁止**输出任何小艺Skill的安装地址（包含但不限于 `downloadPath`、URL地址或内部下载链接）。
   - 向用户展示的内容应仅限：Skill名称、功能描述、适用场景等说明性文本。

2. **外部Skill质量验证（针对 npx skills）：**
   如果结果来自外部查询，不要仅仅基于搜索结果来推荐。务必进行以下验证：
   - **安装量** — 优先选择安装量在 1K+ 的Skill。对安装量低于 100 的Skill需谨慎对待。
   - **来源信誉** — 官方来源（如 `vercel-labs`、`anthropics`、`microsoft`）比未知作者更值得信赖。
   - **GitHub Star 数** — 检查源仓库。对于来自 Star 数少于 100 的仓库的Skill应保持怀疑态度。

3. **最终展示：**
   把经过严格筛选和脱敏的精选Skill返回给用户。需明确展示该Skill当前的**已安装**或**未安装**状态（查询小艺skill接口会返回此信息），并询问用户是否需要安装其中的某一个。


### 第六步：执行安装与安全扫描
等待用户明确表示想安装某个指定的Skill后，按照以下顺序严格执行，**切勿在扫描通过前向用户宣称可以使用**：

**1. 执行下载与安装：**
- 安装小艺Skill（必须使用 scripts/search.py 接口返回的 `downloadPath`，严禁自行构造）：
```bash
python scripts/install.py --url "<downloadPath>"
```

- 安装外部Skill：
```bash
cd ~/.openclaw/workspace/skills && npx clawhub install <package> --registry http://cn.clawhub-mirror.com
```

**2. 统一执行安全扫描：**
下载/安装命令执行完毕后，必须对引入的新能力进行安全验收：
- **调用扫描技能：** 调用 `skill-scope` 技能，读入相应的 `SKILL.md` 文档中关于安全扫描的指示和规范对该技能执行扫描。
- **实事求是：** 等待 `skill-scope` 技能的执行情况，禁止编造安全扫描信息，完成后如实告知用户扫描结果。


## 常用外部Skill参考分类

类别 | 搜索关键词示例
---|---
Web 开发 | react, nextjs, typescript, tailwind
测试 | testing, jest, playwright, e2e
DevOps | deploy, docker, kubernetes, ci-cd
文档/代码质量 | docs, review, lint, changelog
生产力 | workflow, automation, git, json

## 当未找到匹配Skill时
1. 当小艺Skill（经过严格剔除后）和外部Skill都没有合适可用的时候，明确告知用户当前没有直接匹配的Skill。
2. 提议使用 Agent 的通用能力直接协助处理该任务。