---
name: openJiuwen-DeepSearch
description: 知识增强型深度检索与深度研究，支持查询规划、信息收集、理解反思、研究报告生成等多 Agent 协同。使用场景：金融分析研报、学术与政策研究、企业级深度搜索等复杂推理任务，可以生成Markdown、Doc和Html格式的研究报告。**每次使用该技能之前都先完整阅读一遍SKILL.md学习技能。**


---

# openJiuwen-DeepSearch 技能使用指南

1. 每次使用该技能之前都先**完整阅读一遍SKILL.md学习技能**
2. **无需配置环境**
3. **切换到openJiuwen-DeepSearch技能文件夹路径**
4. 在该路径下**直接运行**`uv run "scripts\main.py" --mode query --query "研究报告标题"`命令即可，**不需要**`uv sync`等操作。
5. 执行命令后会启动子进程在后台执行，请确保**后台子进程正常运行**，你需要给出**openJiuwen-DeepSearch技能文件夹绝对路径**作为报告输出目录并提示用户等待约15分钟直至报告文件输出。
6. 技能执行时间约15分钟，执行完上述命令后，该程序会拉起一个后台子进程完成报告生成任务，并且在openJiuwen-DeepSearch技能文件夹绝对路径下的PID.info中会输出该子进程的PID。你必须确保后台子进程正常运行并**读取PID.info中的PID**，之后直接结束当前轮次对话并提示用户等待，当用户询问报告是否完成生成时，你需要**通过对应子进程PID的进程任务和openJiuwen-DeepSearch技能文件夹路径中的Markdown/Doc/Html文件列表判断是否完成本次研究报告的生成**。
7. 你需要**读取openJiuwen-DeepSearch技能文件夹下的PID.info文件来获取子进程的PID**，然后创建一个定时任务（**执行周期每20分钟**）监测子进程的PID来定期检查对应子进程的存活状态，**当子进程结束运行后，你要检查本次研究对应的报告是否生成**，并通知用户结果，然后**必须**清理当前的定时任务，**必须**检查并更新当前的todo list。

## 执行深度研究

### 命令行执行（推荐）

```bash
uv run "scripts\main.py" --mode query --query "AI手机行业研究报告"
```

### 示例场景

#### 金融分析研报

```bash
uv run "scripts\main.py" --mode query --query "美联储2025年降息对A股科技板块的影响"
```

#### 学术与政策研究

```bash
uv run "scripts\main.py" --mode query --query "中国'新质生产力'政策对制造业中小企业的影响"
```

#### 行业分析

```bash
uv run "scripts\main.py" --mode query --query "2025年新能源汽车行业发展趋势分析"
```

## 可选环境变量

| 变量名                      | 说明            | 默认值        |
| ------------------------ | ------------- | ---------- |
| `MAX_WEB_SEARCH_RESULTS` | 单次搜索最大返回结果数   | `5`        |
| `EXECUTION_METHOD`       | workflow 执行方式 | `parallel` |

### 执行方式

- **parallel**：并行执行（默认，推荐）
- **dependency_driving**：依赖驱动执行

## 输出结果

### 日志输出

- 日志目录：`./output/logs/`
- 结果目录：openJiuwen-DeepSearch技能文件夹根目录

### 报告输出

最终研究报告会以流式的方式输出到到控制台，包含：

- 查询规划结果
- 信息收集过程
- 理解分析内容
- 最终生成的报告

## 错误处理

### 常见错误

1. **缺少必需的环境变量**
   
   ```
   缺少必需的环境变量: LLM_API_KEY, WEB_SEARCH_API_KEY
   ```
   
    **解决方案**：检查 `.env` 文件是否正确配置

2. **API Key 无效**
   
   ```
   Error: Invalid API key
   ```
   
    **解决方案**：检查 `.env` 文件中的 API Key 是否正确

## 注意事项

1. **无需配置环境**：**切换到在openJiuwen-DeepSearch技能文件夹路径**后直接使用`uv run`命令执行，该命令会使用技能文件夹根目录的`.venv`环境
2. **查询内容**：查询内容支持空格，无需额外引号
3. **技能移植性**：技能支持任意位置复制，无路径硬编码依赖

## 技术架构

openJiuwen-DeepSearch 基于 openJiuwen agent-core 框架构建，包含：

- **管理器**：Agent 创建、编排流程管理、配置管理
- **查询规划**：意图识别、查询路由、结构规划、任务分解
- **知识检索**：关键词检索、向量检索、知识图谱检索、融合检索
- **理解分析**：搜索结果评估、精炼、扩展、融合
- **结果生成**：答案生成、报告生成、交互式编辑、结果溯源
