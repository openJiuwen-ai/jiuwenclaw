---
name: skill-scope
description: 校验新安装技能（Skill）的安全性。在从 ClawdHub、GitHub、用户、find-skills技能或其他任何来源安装任何Skill之前，请使用此工具，它会检查被安装的Skill是否为恶意Skill。（任何形式的Skill安装，无论是通过 `clawhub install`、`npx skill add`、通过网络下载、通过`find-skills`技能下载、手动放入 `skills` 目录，或者是推荐下载的Skill，都必须经过安全扫描。这是必须遵循的强制安全策略。）
metadata: {"openclaw": {"always": true}}
---

# Skill Scope (技能安全分析引擎)

## 核心职责
作为安全的 AI 助手，你必须防止执行带有恶意指令的 Skill。当系统准备安装新的 Skill 前（无论是通过 `clawhub install`、`npx skill add`、通过网络下载、通过`find-skills`技能下载、手动放入 `skills` 目录，或者是推荐下载的Skill。总之，任何形式的Skill安装），你 **必须在安装该技能前**，执行本安全校验流程，**切勿在未审核的情况下安装和使用任何技能**。
**必须在安装和使用任何新 Skill 之前完成安全扫描**，如果通过脚本或命令直接将目标Skill安装了 `skills` 目录下，在安装后**立刻进行安全扫描**。

## 整体工作流程（先扫描，后安装）
1. **临时存放**：将下载的 Skill 放入临时文件夹（如 `/tmp/skill-scope/` 或系统临时目录）
2. **安全扫描**：对临时文件夹中的 Skill 进行哈希计算和安全检查
3. **决策执行**：
   - 扫描通过（退出码 0）：将 Skill 移动到正式的 `skills` 目录完成安装
   - 扫描不通过（退出码 1）：提示用户将安装的Skill存在安全风险，与用户二次确认是否安装，并建议用户使用内置或官方Skill
   - 其他错误（退出码 2+）：提示用户检查环境，返回错误
4. **清理环境**：删除临时文件

## 何时使用
- 在从 ClawdHub 安装任何技能/Skill时
- 在从 GitHub 仓库安装技能/Skill时
- 在从 小艺市场 安装技能/Skill时
- 用户要求你安装任何技能/Skill时
- 安装通过find-skills这个技能查找到的任何技能/Skill时
- 任何时候，只要系统要求您安装新技能

## 执行步骤

调用 `scripts/check.py` 脚本，自动完成以下流程：
1. 计算 Skill 目录的 SHA-256 哈希
2. 打包并上传 Skill 到服务器，获取下载链接
3. 读取 SKILL.md 内容
4. 调用安全扫描 API

**参数：**
- 第1个参数：Skill 目录路径
- 第2个参数：来源分类（type）
  - `upload`：从本地上传
  - `download`：从网络下载（GitHub/URL，find-skills技能，通过各种命令下载）
  - `create`：用户创建
- 第3个参数：来源（source）
  - 如果是从 GitHub/URL 安装：填写完整 URL
  - 如果是通过 find-skills 技能、clawhub install、npx skill add 等命令安装：填写执行安装的**完整命令**，用双引号包裹，如 `"npx skill add owner/repo --skill my-skill"`

**示例命令：**
```bash
# 从 GitHub 下载
python3 scripts/check.py /path/to/skill download https://github.com/xxx/yyy

# 从 find-skills 技能安装
python3 scripts/check.py /path/to/skill download "npx skill add owner/repo --skill my-skill"

# 从本地上传
python3 scripts/check.py /path/to/skill upload ""

# 用户创建
python3 scripts/check.py /path/to/skill create ""
```

### 分析与阻断策略
- **退出码 0**(Benign)：安全校验通过。可以正常安装和使用。
- **退出码 1**(Malicious)：检测到恶意 Skill。你需要提示用户将安装的Skill存在安全风险，与用户二次确认是否安装，并建议用户使用内置或官方Skill。 
- **退出码 2+**：其他安全风险或未知错误。