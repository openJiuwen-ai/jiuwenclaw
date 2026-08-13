# -*- coding: utf-8 -*-
"""v3 规模测试：几百个工具（含 MCP 形态）下，BM25+CJK 还能不能搜到。

模拟：真实 35 工具 + 465 个干扰工具（各种领域中文描述）= 500 个。
跑真实 query，看命中率和延迟。500 个工具是"几百个"的典型量级。
"""
from __future__ import annotations
import sys, io, time
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from jiuwenswarm.common.tool_retrieval.bm25_search import BM25Okapi, tokenize
from jiuwenswarm.common.tool_retrieval.search import haystack_for

class MockTool:
    def __init__(self, name, description="", parameters=None):
        self.name = name; self.description = description; self.parameters = parameters or {}

# ── 真实 35 工具 ──
REAL = {
  "memory_search":"在长期记忆系统中搜索用户的记忆信息。回答关于工作内容、决策、日期、人物、偏好或待办事项的问题之前先调用。",
  "cron_create_job":"创建一个 cron 定时任务，按计划时间自动执行。","cron_list_jobs":"列出所有 cron 定时任务及其运行状态。",
  "cron_delete_job":"删除指定的 cron 定时任务。","cron_update_job":"修改已有定时任务的配置，如时间、目标。",
  "cron_preview_job":"预览 cron 定时任务的下 N 次计划执行时间。","cron_toggle_job":"启用或停用一个定时任务。",
  "cron":"使用 action 接口：status、list、add、update、remove、run、runs、wake。定时任务管理。",
  "todo_create":"创建一个新的待办事项。","todo_list":"列出所有待办事项及完成状态。","todo_modify":"修改或完成待办事项。",
  "send_file_to_user":"向用户发送文件，如报告、图片、文档。","fetch_webpage":"配合 free_search 使用：先搜索再抓取结果页。抓取网页文本，返回状态码、标题和正文。",
  "free_search":"免费搜索，返回结果 URL 和摘要。用户询问最新、当前、实时信息时使用。","read_file":"读取本地文件内容。",
  "write_file":"写入内容到本地文件。","list_files":"列出目录下的文件。","powershell":"执行 PowerShell 命令。",
  "session_new":"创建后台会话任务/子代理会话。","session_list":"查看所有协程列表及其状态。",
  "wiki_query":"查询知识库，自然语言查询 wiki。","wiki_ingest":"向知识库写入/导入内容。","wiki_lint":"知识库内容检查/校验。",
  "search_skill":"搜索技能市场中可安装的技能。","install_skill":"安装技能。","skill_tool":"技能市场入口，搜索、安装、管理技能。",
  "task_tool":"委派子代理执行任务，并行或后台子任务。","audio_metadata":"读取音频元数据信息，如时长。",
  "write_memory":"在 memory 目录下创建或更新记忆文件。","edit_memory":"编辑修改已有的记忆文件内容。","read_memory":"读取记忆文件内容。",
  "memory_get":"获取指定的记忆条目。","evolve_review_task":"审查自演进任务。","load_tools":"加载/注入工具集。",
  "search_tools":"按需检索工具，用关键词查找可用工具。",
}
# ── 465 个干扰工具（模拟 MCP 各领域工具，中文描述）──
DOMAINS = [
  ("git_","代码版本管理，git 仓库操作，提交、分支、合并、冲突解决。"),
  ("docker_","容器镜像管理，docker 构建推送运行，容器编排。"),
  ("k8s_","Kubernetes 集群管理，部署、扩缩容、服务发现、ingress 配置。"),
  ("mysql_","MySQL 数据库查询，建表、索引、事务、备份恢复。"),
  ("redis_","Redis 缓存操作，键值、哈希、列表、发布订阅、过期策略。"),
  ("pdf_","PDF 文档处理，解析、合并、拆分、加水印、提取文本。"),
  ("excel_","Excel 表格处理，读取、写入、公式、格式化、数据透视。"),
  ("email_","邮件收发，SMTP/IMAP，附件、模板、群发、定时邮件。"),
  ("calendar_","日历日程管理，创建、查询、提醒、会议室预订、冲突检测。"),
  ("weather_","天气预报查询，实时气温、降水、空气质量、未来一周。"),
  ("translate_","文本翻译，多语言互译，术语库、记忆库、批量翻译。"),
  ("image_","图片处理，裁剪、缩放、滤镜、格式转换、水印、OCR 识别。"),
  ("video_","视频处理，剪辑、转码、合并、字幕、缩略图、流媒体。"),
  ("audio_","音频处理，转码、剪辑、混音、降噪、格式转换、语音转文字。"),
  ("chart_","图表生成，柱状图、折线图、饼图、散点、数据可视化。"),
  ("ssh_","远程服务器 SSH 连接，执行命令、传输文件、端口转发。"),
  ("ftp_","FTP 文件传输，上传、下载、目录遍历、断点续传。"),
  ("s3_","对象存储 S3，桶管理、上传、下载、生命周期、权限策略。"),
  ("jira_","Jira 项目管理，创建工单、查询、状态流转、看板、 sprint。"),
  ("confluence_","Confluence 文档协作，创建、编辑、搜索、空间、权限。"),
  ("slack_","Slack 消息通知，发送、频道、线程、文件、提醒机器人。"),
  ("dingtalk_","钉钉消息推送，工作通知、群机器人、待办、审批回调。"),
  ("feishu_","飞书多维表格，记录、字段、视图、自动化、文档。"),
  ("notion_","Notion 页面管理，数据库、属性、关联、模板、导出。"),
  ("analytics_","数据分析统计，PV/UV、漏斗、留存、 cohort、报表。"),
  ("ml_","机器学习训练，特征工程、模型训练、评估、超参调优、推理。"),
  ("log_","日志查询分析，采集、过滤、聚合、告警、全链路追踪。"),
  ("monitor_","监控告警，指标采集、阈值、通知、 dashboard、巡检。"),
  ("cert_","证书管理，申请、续期、部署、吊销、HTTPS 配置。"),
  ("dns_","域名解析 DNS，记录管理、健康检查、智能解析、容灾。"),
  ("cdn_","CDN 内容分发，缓存、刷新、预热、回源、防盗链。"),
]
EXTRA = {}
i = 0
for prefix, desc in DOMAINS:
    for action in ["create","list","delete","update","get","search","export","import","batch","query","run","check","test","deploy","backup","restore","sync","validate","parse","format"]:
        nm = f"{prefix}{action}"
        EXTRA[nm] = f"{desc} {action} 操作。"
        i += 1
        if len(EXTRA) >= 465: break
    if len(EXTRA) >= 465: break
# 补齐到 465
while len(EXTRA) < 465:
    EXTRA[f"misc_tool_{len(EXTRA)}"] = "杂项工具，处理一般性任务。"

ALL = {**REAL, **EXTRA}
TOOLS = [MockTool(n, d) for n, d in ALL.items()]
NAMES = list(ALL.keys())

print(f"工具总数：{len(TOOLS)}（真实 {len(REAL)} + 干扰 {len(EXTRA)}）")

# ── 建索引 ──
t0 = time.time()
corpus = [tokenize(haystack_for(t, 256)) for t in TOOLS]
t_build = time.time() - t0
idx = BM25Okapi(corpus)
t_idx = time.time() - t0
print(f"建索引耗时：{t_idx*1000:.1f} ms（含 tokenize {t_build*1000:.1f} ms）")

# ── 测真实 query + 纯中文 query + MCP 形态 query ──
CASES = [
    ("cron_create_job 创建定时任务", {"cron_create_job"}, "带工具名"),
    ("send_file_to_user 发送文件给用户", {"send_file_to_user"}, "带工具名"),
    ("memory_search 搜索记忆", {"memory_search"}, "带工具名"),
    ("帮我建个定时任务", {"cron_create_job"}, "纯中文同义"),
    ("发文件给用户", {"send_file_to_user"}, "纯中文"),
    ("查一下知识库", {"wiki_query"}, "纯中文"),
    ("删掉那个定时任务", {"cron_delete_job"}, "纯中文同义"),
    ("docker 容器镜像构建推送", {n for n in NAMES if n.startswith("docker")}, "MCP领域"),
    ("Kubernetes 集群部署扩缩容", {n for n in NAMES if n.startswith("k8s")}, "MCP领域"),
    ("PDF 文档解析合并加水印", {n for n in NAMES if n.startswith("pdf")}, "MCP领域"),
    ("Excel 表格读取写入公式", {n for n in NAMES if n.startswith("excel")}, "MCP领域"),
    ("机器学习模型训练评估推理", {n for n in NAMES if n.startswith("ml")}, "MCP领域"),
]
print(f"\n{'query':<32} {'top1':<22} {'top3':<30} 命中 延迟ms")
print("-" * 100)
hit = 0
t_q_total = 0
for q, exp, note in CASES:
    t1 = time.time()
    qt = tokenize(q)
    scores = idx.get_scores(qt)
    ranked = sorted(((s, NAMES[i]) for i, s in enumerate(scores) if s > 0), key=lambda x: (-x[0], x[1]))
    t_q = (time.time() - t1) * 1000
    t_q_total += t_q
    top3 = [n for _s, n in ranked[:3]]
    top1 = top3[0] if top3 else "(无)"
    ok = top1 in exp
    ok3 = bool(set(top3) & exp)
    hit += ok
    print(f"{q[:30]:<32} {top1[:20]:<22} {','.join(top3)[:28]:<30} {'✓' if ok else ('top3✓' if ok3 else '✗')} {t_q:.1f}")

print("-" * 100)
print(f"\ntop1 命中：{hit}/{len(CASES)}")
print(f"平均查询延迟：{t_q_total/len(CASES):.1f} ms（{len(TOOLS)} 工具）")
print(f"\n对比：ratel 默认也是 BM25，文档说'runs in your process, deterministically'——纯内存算，几百文档是毫秒级。")
