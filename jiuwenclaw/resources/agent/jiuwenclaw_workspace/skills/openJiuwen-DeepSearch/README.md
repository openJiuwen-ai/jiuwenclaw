# openJiuwen-DeepSearch 技能

知识增强型深度检索与研究引擎，支持查询规划、信息收集、理解反思、报告生成等多 Agent 协同处理能力。

## 功能特性

- **深度研究**：基于用户查询自动规划任务、收集信息、分析并生成研究报告
- **知识增强**：融合本地知识库与网页搜索，提升搜索质量
- **结果溯源**：输出结果包含引用信息，支持片段级溯源
- **图文并茂**：支持包含图表的可视化报告生成
- **多 Agent 协同**：查询规划、信息收集、理解分析、报告生成全流程自动化

## 适用场景

- **金融分析研报**：投资分析、行业研究、市场趋势分析
- **学术与政策研究**：政策影响分析、学术文献综述
- **企业级深度搜索**：复杂信息查询、多源数据整合

## 快速开始

### 1. uv 环境准备

**安装 uv（如未安装）：**

```bash
# Windows (PowerShell)
pip install uv

# Linux/Mac
pip install uv
```

### 2. 配置环境

使用 uv 创建 Python 3.11 虚拟环境并安装依赖（精确版本）：

```bash
# 使用 Python 3.11 创建虚拟环境并安装精确版本的依赖
uv venv --python 3.11
uv pip install openjiuwen-deepsearch==0.1.1 python-dotenv pypandoc markdown markdown_mermaid_cli -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. 配置 API Key

复制示例配置文件并编辑：

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入你的 API Key：

```env
# LLM 配置
LLM_MODEL_NAME=gpt-4o
LLM_MODEL_TYPE=openai
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-your-actual-openai-api-key-here

# 搜索引擎配置
WEB_SEARCH_ENGINE_NAME=tavily
WEB_SEARCH_API_KEY=tvly-your-actual-tavily-api-key-here
WEB_SEARCH_URL=https://api.tavily.com
```

### 4. 手动执行深度研究（可跳过，后续由Agent执行命令）

```bash
uv run scripts\main.py --mode query --query "待生成深度调研报告的主题"
```


## 输出结果

- 日志目录：`./output/logs/`
- 结果目录：openJiuwen-DeepSearch技能文件夹根目录


## 许可证

Apache 2.0 License
