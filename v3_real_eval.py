# -*- coding: utf-8 -*-
"""v3 真实 query 离线测评：用 122 条 LLM 真实发的 search query，喂给 v3 BM25+CJK，
判命中，并和 v2 当时实际返回对比。

语料：35 个真实工具的真实中文 description。
判命中：query 里若带工具名 → 返回里有它即命中；
       不带工具名的英文词（todo list / send file）→ 返回里含语义对应工具即命中；
       纯中文 → 看 top3 里有没有"期望工具集合"。
"""
from __future__ import annotations
import sys, io, json, glob, os, re
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from jiuwenswarm.common.tool_retrieval.bm25_search import BM25Okapi, tokenize
from jiuwenswarm.common.tool_retrieval.search import haystack_for

class MockTool:
    def __init__(self, name, description="", parameters=None):
        self.name = name; self.description = description; self.parameters = parameters or {}
    def __repr__(self): return f"MockTool({self.name!r})"

# ── 真实语料 ──
DESC = {
  "memory_search":"在长期记忆系统中搜索用户的记忆信息。回答关于工作内容、决策、日期、人物、偏好或待办事项的问题之前先调用。",
  "write_memory":"在 memory 目录下创建或更新记忆文件，写入记忆相关内容，如 USER.md、MEMORY.md。禁止用于创建代码或配置文件。",
  "edit_memory":"编辑修改已有的记忆文件内容。",
  "read_memory":"读取记忆文件内容。",
  "memory_get":"获取指定的记忆条目。",
  "cron":"使用 action 接口：status、list、add、update、remove、run、runs、wake。定时任务管理。",
  "cron_create_job":"创建一个 cron 定时任务，按计划时间自动执行。",
  "cron_list_jobs":"列出所有 cron 定时任务及其运行状态。",
  "cron_delete_job":"删除指定的 cron 定时任务。",
  "cron_update_job":"修改已有定时任务的配置，如时间、目标。",
  "cron_preview_job":"预览 cron 定时任务的下 N 次计划执行时间。",
  "cron_toggle_job":"启用或停用一个定时任务。",
  "todo_create":"创建一个新的待办事项。",
  "todo_list":"列出所有待办事项及完成状态。",
  "todo_modify":"修改或完成待办事项。",
  "send_file_to_user":"向用户发送文件，如报告、图片、文档。",
  "fetch_webpage":"通常配合 paid_search 或 free_search 使用：先搜索再抓取结果页。抓取网页文本，返回状态码、标题和正文。适用于文档、博客、新闻、API参考。",
  "free_search":"免费搜索，返回结果 URL 和摘要。paid_search 可用时优先用 paid_search，free_search 作为兜底或用户明确要求免费搜索时使用。用户询问最新、当前、实时信息时使用。",
  "read_file":"读取本地文件内容。",
  "write_file":"写入内容到本地文件。",
  "list_files":"列出目录下的文件。",
  "powershell":"执行 PowerShell 命令。",
  "session_new":"创建后台会话任务/子代理会话。",
  "session_list":"查看所有协程列表及其状态。",
  "wiki_query":"查询知识库，自然语言查询 wiki。",
  "wiki_ingest":"向知识库写入/导入内容。",
  "wiki_lint":"知识库内容检查/校验。",
  "search_skill":"搜索技能市场中可安装的技能。",
  "install_skill":"安装技能。",
  "skill_tool":"技能市场入口，搜索、安装、管理技能。",
  "task_tool":"委派子代理执行任务，并行或后台子任务。",
  "evolve_review_task":"审查自演进任务。",
  "load_tools":"加载/注入工具集。",
  "search_tools":"按需检索工具，用关键词查找可用工具。",
  "audio_metadata":"读取音频元数据信息，如时长。",
}
TOOLS = [MockTool(n, d) for n, d in DESC.items()]
NAMES = list(DESC.keys())

# ── 期望判定：query → 期望工具集合 ──
# 从 query 提工具名；查不到的，手工标"语义期望"（基于 query 意图）
def expected_for(qtext):
    # 1. 直接抽 query 里的完整工具名 token
    found = [n for n in NAMES if n in qtext]
    if found:
        return set(found)
    # 2. 意图映射（query 不带工具名时，按语义标期望）
    q = qtext.lower()
    m = {
        # 文件发送：send file / 发送文件 → send_file_to_user
        "send file": {"send_file_to_user"}, "发送文件": {"send_file_to_user"},
        "发给用户": {"send_file_to_user"},
        # 记忆
        "搜索记忆": {"memory_search"}, "回忆": {"memory_search"}, "记忆": {"memory_search","write_memory"},
        "写记忆": {"write_memory"}, "保存偏好": {"write_memory"}, "写入长期记忆": {"write_memory"},
        # 定时任务
        "定时任务": {"cron_create_job","cron_list_jobs","cron"}, "创建定时任务提醒": {"cron_create_job"},
        "定时任务提醒": {"cron_create_job"}, "cron job": {"cron_create_job","cron"},
        # 待办
        "待办": {"todo_create","todo_list"}, "todo list": {"todo_list"}, "todo create": {"todo_create"},
        "todo": {"todo_create","todo_list"},
        # wiki/知识库
        "知识库": {"wiki_query"}, "wiki": {"wiki_query"}, "wiki查询": {"wiki_query"},
        # 技能
        "技能": {"search_skill","skill_tool","install_skill"}, "skill market": {"skill_tool","search_skill"},
        "ppt": {"search_skill","skill_tool"}, "install skill": {"install_skill"}, "安装技能": {"install_skill"},
        # 后台会话
        "后台会话": {"session_new"}, "session new": {"session_new"}, "subagent": {"task_tool"},
        # 音频
        "audio metadata": {"audio_metadata"}, "音频": {"audio_metadata"}, "时长": {"audio_metadata"},
        # 网页搜索
        "web search": {"free_search"}, "网页搜索": {"free_search"}, "免费搜索": {"free_search"},
        "网络搜索": {"free_search"}, "internet": {"free_search"},
        # 文件读写（read_file/write_file 只在明确时标）
        "download": {"send_file_to_user"},
    }
    exp = set()
    for k, v in m.items():
        if k in q: exp |= v
    return exp

# ── 建 v3 BM25+CJK 索引 ──
corpus = [tokenize(haystack_for(t, 256)) for t in TOOLS]
idx = BM25Okapi(corpus)

# ── 跑 122 条真实 query ──
files = glob.glob(os.path.expanduser("~/.jiuwenswarm/agent/.logs/prompt_capture/*_prompt.json"))
rows = []
for f in files:
    try: d = json.load(open(f, encoding="utf-8"))
    except: continue
    for q in d.get("summary",{}).get("search_tools",{}).get("queries",[]):
        raw = q.get("query","")
        mt = re.search(r'"query"\s*:\s*"([^"]+)"', raw)
        qtext = mt.group(1) if mt else raw
        tk = re.search(r'"top_k"\s*:\s*(\d+)', raw)
        topk = int(tk.group(1)) if tk else 3
        rows.append((qtext, topk, q.get("returned", [])))

v3_hit = 0; v3_top3_hit = 0; v3_empty = 0
v2_hit = 0; v2_empty = 0
both_wrong = 0; v3_fix = 0; v3_break = 0
details = []
for qtext, topk, v2_ret in rows:
    exp = expected_for(qtext)
    # v3 检索
    qt = tokenize(qtext)
    scores = idx.get_scores(qt)
    ranked = sorted(((s, NAMES[i]) for i, s in enumerate(scores) if s > 0), key=lambda x: (-x[0], x[1]))
    v3_ret = [n for _s, n in ranked[:topk]]
    v3_top3 = [n for _s, n in ranked[:3]]
    if not v3_ret: v3_empty += 1
    if not v2_ret: v2_empty += 1
    # 命中判定（exp 非空时）
    if exp:
        v3_ok = bool(set(v3_ret) & exp)
        v3_top3_ok = bool(set(v3_top3) & exp)
        v2_ok = bool(set(v2_ret) & exp)
        if v3_ok: v3_hit += 1
        if v3_top3_ok: v3_top3_hit += 1
        if v2_ok: v2_hit += 1
        if not v2_ok and v3_ok: v3_fix += 1      # v2 错、v3 修对了
        if v2_ok and not v3_ok: v3_break += 1    # v2 对、v3 弄坏了
        if not v2_ok and not v3_ok: both_wrong += 1
    details.append((qtext, topk, exp, v2_ret, v3_ret))

n = len(rows)
n_exp = sum(1 for d in details if d[2])  # d[2]=exp，能判定的条数

print("=" * 100)
print(f"v3 真实 query 离线测评：{n} 条 LLM 真实 search query | 真实语料 {len(TOOLS)} 工具")
print("=" * 100)
print(f"\n【可判定命中（{n_exp} 条，有明确期望）】")
print(f"  v3 top-k 命中率 : {v3_hit}/{n_exp} = {v3_hit/n_exp:.0%}   （按 query 自带 top_k）")
print(f"  v3 top-3 命中率 : {v3_top3_hit}/{n_exp} = {v3_top3_hit/n_exp:.0%}   （放宽到 top3）")
print(f"  v2 当时命中率   : {v2_hit}/{n_exp} = {v2_hit/n_exp:.0%}")
print(f"\n【空结果】")
print(f"  v3 空结果 : {v3_empty}/{n} = {v3_empty/n:.0%}")
print(f"  v2 空结果 : {v2_empty}/{n} = {v2_empty/n:.0%}")
print(f"\n【v2 vs v3 修复对比】")
print(f"  v2 错 → v3 修对 : {v3_fix}")
print(f"  v2 对 → v3 弄坏 : {v3_break}")
print(f"  两者都错       : {both_wrong}")

print(f"\n{'query':<40} {'期望':<26} {'v2返回':<26} {'v3返回':<26} 判定")
print("-" * 130)
for qtext, topk, exp, v2_ret, v3_ret in details:
    if not exp:
        tag = "?"
    else:
        v3ok = bool(set(v3_ret) & exp); v2ok = bool(set(v2_ret) & exp)
        tag = ("v3✓v2✗ 修复!" if v3ok and not v2ok else
               "v3✓v2✓" if v3ok and v2ok else
               "v3✗v2✓ 退化!" if not v3ok and v2ok else
               "都✗")
    e = ",".join(sorted(exp)) if exp else "(无法判定)"
    print(f"{qtext[:38]:<40} {e[:24]:<26} {','.join(v2_ret)[:24]:<26} {','.join(v3_ret)[:24]:<26} {tag}")

print("\n--- v3 仍错的用例（看短板）---")
for qtext, topk, exp, v2_ret, v3_ret in details:
    if exp and not (set(v3_ret) & exp):
        print(f"  ✗ {qtext}")
        print(f"    期望={sorted(exp)}  v3={v3_ret}  v2={v2_ret}")
