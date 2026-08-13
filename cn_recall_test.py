# -*- coding: utf-8 -*-
"""中文召回对比：v3 BM25+CJK n-gram 分词 vs v2 旧分词（whitespace split）。

目的：验证 v3 用 CJK n-gram 让 BM25 单扛纯中文 query 时，是否真能命中
正确工具（v2 旧分词会把 `创建定时任务` 当一个不透明 token，纯中文 query
在 BM25 侧静默 0 命中）。

不联网、不加载 fastembed/dense——纯 BM25 路径，这正是 v3 默认形态。
"""
from __future__ import annotations

import sys, io
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from jiuwenswarm.common.tool_retrieval.bm25_search import BM25Okapi, tokenize as v3_tokenize
from jiuwenswarm.common.tool_retrieval.search import haystack_for


class MockTool:
    def __init__(self, name, description="", parameters=None):
        self.name = name
        self.description = description
        self.parameters = parameters or {}
    def __repr__(self):
        return f"MockTool({self.name!r})"


# ---- 贴近真实的中文工具集（40 个，name 英文 + description 中文）----
TOOLS = [
    MockTool("cron_create_job", "创建一个 cron 定时任务，按计划时间自动执行。"),
    MockTool("cron_list_jobs", "列出所有 cron 定时任务及其运行状态。"),
    MockTool("cron_delete_job", "删除指定的 cron 定时任务。"),
    MockTool("cron_update_job", "修改已有定时任务的配置，如时间、目标。"),
    MockTool("cron_preview_job", "预览定时任务的下 N 次计划执行时间。"),
    MockTool("cron_toggle_job", "启用或停用一个定时任务。"),
    MockTool("memory_search", "在长期记忆系统中搜索用户的工作内容、决策、偏好。"),
    MockTool("memory_save", "将当前对话的重要信息保存到长期记忆。"),
    MockTool("memory_delete", "删除记忆系统中的某条记录。"),
    MockTool("todo_create", "创建一个新的待办事项。"),
    MockTool("todo_list", "列出所有待办事项及完成状态。"),
    MockTool("todo_complete", "将待办事项标记为已完成。"),
    MockTool("send_file_to_user", "向用户发送文件，如报告、图片、文档。"),
    MockTool("web_file_download", "从网络下载文件到本地。"),
    MockTool("read_terminal_output", "读取终端命令的输出内容。"),
    MockTool("release_terminal", "释放终端资源，命令完成后必须调用。"),
    MockTool("session_cancel", "根据 session_id 取消正在运行的协程。"),
    MockTool("session_list", "查看所有协程列表及其状态。"),
    MockTool("wiki_search", "在知识库中搜索文档和页面。"),
    MockTool("wiki_create", "在知识库中创建一个新页面。"),
    MockTool("wiki_update", "编辑修改知识库中的页面内容。"),
    MockTool("alarm_create", "在手机上创建一个闹钟提醒。"),
    MockTool("alarm_list", "列出手机上所有闹钟。"),
    MockTool("alarm_delete", "删除手机上的闹钟。"),
    MockTool("image_generate", "根据文字描述生成图片。"),
    MockTool("image_edit", "编辑修改已有图片。"),
    MockTool("bash_exec", "执行 shell 命令。"),
    MockTool("read_file", "读取本地文件内容。"),
    MockTool("write_file", "写入内容到本地文件。"),
    MockTool("edit_file", "编辑修改本地文件。"),
    MockTool("search_web", "搜索互联网获取网页信息。"),
    MockTool("send_email", "发送电子邮件给指定联系人。"),
    MockTool("create_calendar_event", "在日历中创建一个日程事件。"),
    MockTool("list_calendar_events", "列出日历中的日程安排。"),
    MockTool("translate_text", "将文本翻译成指定语言。"),
    MockTool("summarize_document", "对长文档进行摘要总结。"),
    MockTool("create_chart", "根据数据生成图表。"),
    MockTool("search_tools", "按需检索可用的工具，用关键词查找。"),
    MockTool("skill_run", "运行一个已安装的技能。"),
    MockTool("skill_list", "列出所有可用的技能。"),
]

# ---- 测试用例：(query, 期望命中的工具名集合, 说明) ----
CASES = [
    ("我想创建一个定时任务", {"cron_create_job"}, "纯中文，动词+名词，无英文"),
    ("怎么设置闹钟提醒", {"alarm_create"}, "纯中文同义词：设置≈创建，提醒≈闹钟"),
    ("帮我存一下刚才的对话", {"memory_save"}, "纯中文：存≈保存，对话"),
    ("看看我有哪些待办", {"todo_list"}, "纯中文：待办≈todo，看看≈list"),
    ("删掉那个定时任务", {"cron_delete_job"}, "纯中文：删掉≈delete"),
    ("改一下闹钟时间", {"alarm_create"}, "纯中文：改≈update（无 update 工具，退 alarm_create）"),
    ("下载网上的文件", {"web_file_download"}, "纯中文：下载、文件"),
    ("把报告发给用户", {"send_file_to_user"}, "纯中文：发、用户、文件"),
    ("读一下终端的输出", {"read_terminal_output"}, "纯中文：读、终端、输出"),
    ("取消那个协程", {"session_cancel"}, "纯中文：取消、协程"),
    ("查一下知识库里有没有", {"wiki_search"}, "纯中文：查≈search，知识库"),
    ("生成一张图片", {"image_generate"}, "纯中文：生成、图片"),
    ("翻译这段文字", {"translate_text"}, "纯中文：翻译、文字≈text"),
    ("总结一下这个文档", {"summarize_document"}, "纯中文：总结≈summarize，文档≈document"),
    ("画个图表", {"create_chart"}, "纯中文：画≈create，图表≈chart"),
    ("运行那个技能", {"skill_run"}, "纯中文：运行≈run，技能≈skill"),
]


def old_tokenize(text: str):
    """v2 旧分词：lowercase + whitespace split（中文整段当一个 token）。"""
    if not text:
        return []
    return [t for t in str(text).lower().split() if t]


def run_index(tokenizer):
    """用给定分词器建索引并检索所有 case。"""
    # 建一个用指定 tokenizer 的索引
    tools = TOOLS
    corpus_tokens = [tokenizer(haystack_for(t, 256)) for t in tools]
    idx = BM25Okapi(corpus_tokens)
    results = []
    for query, expected, note in CASES:
        q_tokens = tokenizer(query)
        scores = idx.get_scores(q_tokens)
        scored = [(s, tools[i].name) for i, s in enumerate(scores) if s > 0.0]
        scored.sort(key=lambda x: (-x[0], x[1]))
        top5 = [n for _s, n in scored[:5]]
        top1 = top5[0] if top5 else None
        hit = top1 in expected if top1 else False
        results.append({
            "query": query, "expected": expected, "top1": top1,
            "top5": top5, "hit": hit, "note": note,
            "q_tokens": q_tokens,
        })
    return results


def main():
    print("=" * 80)
    print("中文召回对比：v3 BM25+CJK n-gram  vs  v2 旧分词(whitespace split)")
    print("=" * 80)
    print(f"工具集：{len(TOOLS)} 个 | 测试用例：{len(CASES)} 个（全纯中文 query，无英文混入）\n")

    v3 = run_index(v3_tokenize)
    v2 = run_index(old_tokenize)

    print(f"{'query':<26} {'v3 top1':<22} {'v2 top1':<22} {'期望':<22} 结果")
    print("-" * 100)
    v3_hit = 0
    v2_hit = 0
    for r3, r2 in zip(v3, v2):
        exp = next(iter(r3["expected"]))
        v3_ok = "✓" if r3["hit"] else "✗"
        v2_ok = "✓" if r2["hit"] else "✗"
        v3_hit += r3["hit"]
        v2_hit += r2["hit"]
        v3_top = r3["top1"] or "(无命中)"
        v2_top = r2["top1"] or "(无命中)"
        print(f'{r3["query"]:<24} {v3_top:<22} {v2_top:<22} {exp:<22} v3={v3_ok} v2={v2_ok}')

    print("-" * 100)
    n = len(CASES)
    print(f"\n命中率：v3(BM25+CJK) = {v3_hit}/{n} = {v3_hit/n:.0%}   |   v2(旧分词) = {v2_hit}/{n} = {v2_hit/n:.0%}")

    print("\n--- v2 旧分词为何 0 命中（分词样本）---")
    for q, _, _ in CASES[:3]:
        print(f"  query={q}")
        print(f"    v2 tokens = {old_tokenize(q)}   ← 中文整段一个 token，语料里没有这个词→0分")
        print(f"    v3 tokens = {v3_tokenize(q)}")

    print("\n--- v3 未命中的用例分析 ---")
    for r3 in v3:
        if not r3["hit"]:
            print(f"  ✗ {r3['query']}  → top1={r3['top1']}  top5={r3['top5']}  期望={r3['expected']}")
            print(f"     ({r3['note']})")


if __name__ == "__main__":
    main()
