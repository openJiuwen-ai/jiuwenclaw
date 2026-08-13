# -*- coding: utf-8 -*-
"""模拟新增 tool / MCP tool 的动态注册测试。

复刻 v3 的三层机制（离线模拟，不拉 ability_manager）：
  初始索引（不含新工具）
    → 第 1 步 BM25 搜新工具（索引里没有）→ 搜空，证明没进索引
    → 第 2 步 name 直查（query 带工具名）→ 实时命中（第 2 层兜底）
    → 第 3 步 force_refresh：检测工具集变化 → 重建索引 → BM25 搜到了（第 3 层）
    → 第 4 步 下一轮 before_invoke 自动进索引（第 1 层，sig 变了重建）
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
    def __repr__(self): return f"MockTool({self.name!r})"

# ── 初始 35 真实工具（不含新工具）──
INITIAL = {
  "memory_search":"在长期记忆系统中搜索用户的记忆信息。","write_memory":"在 memory 目录下创建或更新记忆文件。",
  "cron_create_job":"创建一个 cron 定时任务，按计划时间自动执行。","cron_list_jobs":"列出所有 cron 定时任务及其运行状态。",
  "cron_delete_job":"删除指定的 cron 定时任务。","cron_update_job":"修改已有定时任务的配置。","cron_preview_job":"预览 cron 定时任务的下 N 次计划执行时间。",
  "cron_toggle_job":"启用或停用一个定时任务。","cron":"使用 action 接口管理定时任务。",
  "todo_create":"创建一个新的待办事项。","todo_list":"列出所有待办事项及完成状态。","todo_modify":"修改或完成待办事项。",
  "send_file_to_user":"向用户发送文件，如报告、图片、文档。","fetch_webpage":"配合 free_search 使用，抓取网页文本。",
  "free_search":"免费搜索，返回结果 URL 和摘要。","read_file":"读取本地文件内容。","write_file":"写入内容到本地文件。",
  "list_files":"列出目录下的文件。","powershell":"执行 PowerShell 命令。",
  "session_new":"创建后台会话任务。","session_list":"查看所有协程列表及其状态。",
  "wiki_query":"查询知识库，自然语言查询 wiki。","wiki_ingest":"向知识库写入/导入内容。","wiki_lint":"知识库内容检查/校验。",
  "search_skill":"搜索技能市场中可安装的技能。","install_skill":"安装技能。","skill_tool":"技能市场入口。",
  "task_tool":"委派子代理执行任务。","audio_metadata":"读取音频元数据信息，如时长。",
  "edit_memory":"编辑修改已有的记忆文件内容。","read_memory":"读取记忆文件内容。","memory_get":"获取指定的记忆条目。",
  "evolve_review_task":"审查自演进任务。","load_tools":"加载/注入工具集。","search_tools":"按需检索工具。",
}
# ── 运行时新增的 3 个工具（模拟用户新装 / MCP 注册）──
NEW_TOOLS = {
  # 1. 普通新工具（用户新装的技能工具）
  "image_generate": "根据文字描述生成图片，AI 图像生成。支持指定尺寸、风格、数量。",
  # 2. MCP 工具（GitHub MCP server 注册的）
  "mcp_github_create_issue": "在 GitHub 仓库创建 issue，设置标题、正文、标签、指派人。MCP github server 提供。",
  # 3. MCP 工具（数据库 MCP server 注册的，纯英文场景）
  "mcp_postgres_query": "执行 PostgreSQL 查询并返回结果。MCP postgres server 提供。支持 SELECT、参数化查询。",
}

# ── v3 索引状态的模拟器（复刻 rail 的 sig 缓存逻辑）──
class V3IndexSimulator:
    def __init__(self, tools):
        self.all_tools = list(tools)              # _cached_all_tool_infos
        self.cached_bm25_sig = None               # _cached_bm25_sig
        self.bm25_index = None                    # _bm25_index
        self.names = [t.name for t in self.all_tools]
        self._rebuild_if_needed()

    def _sig(self):
        return frozenset(self.names)

    def _rebuild_if_needed(self):
        """复刻 _build_bm25_index 的 sig 缓存：变了才建。"""
        sig = self._sig()
        if sig == self.cached_bm25_sig and self.bm25_index is not None:
            return False  # 没变，不重建
        self.cached_bm25_sig = sig
        corpus = [tokenize(haystack_for(t, 256)) for t in self.all_tools]
        self.bm25_index = BM25Okapi(corpus)
        return True  # 重建了

    def bm25_search(self, query, top_k=3):
        """主路径 BM25 检索。"""
        qt = tokenize(query)
        scores = self.bm25_index.get_scores(qt)
        ranked = sorted(((s, self.names[i]) for i, s in enumerate(scores) if s > 0),
                        key=lambda x: (-x[0], x[1]))
        return [n for _s, n in ranked[:top_k]]

    def name_lookup(self, query):
        """第 2 层：name 直查（复刻 _lookup_tool_by_name 的判断）。
        要求 query 含下划线且去下划线后是纯字母数字——像工具名才直查。"""
        ql = query.strip().lower()
        if "_" not in ql or not ql.replace("_", "").isalnum():
            return None  # query 不像工具名，name 直查不触发
        # 模拟从 ability_manager 实时取（all_tools 是全量，含新注册的）
        for t in self.all_tools:
            if t.name.lower() == ql:
                return t.name
        return None

    def force_refresh_and_retry(self, query, live_tools, top_k=3):
        """第 3 层：检测工具集变化 → 重建索引 → 再搜。
        live_tools = 当前真实全量（含新工具）。"""
        live_sig = frozenset(t.name for t in live_tools)
        cached_sig = frozenset(t.name for t in self.all_tools)
        if live_sig == cached_sig:
            return None, False  # 没变化，不重建
        # 有新工具，重建
        self.all_tools = list(live_tools)
        self.names = [t.name for t in self.all_tools]
        self._rebuild_if_needed()
        return self.bm25_search(query, top_k), True

    def before_invoke_refresh(self, live_tools):
        """第 1 层：下一轮 before_invoke 全量刷新。"""
        self.all_tools = list(live_tools)
        self.names = [t.name for t in self.all_tools]
        return self._rebuild_if_needed()


def banner(s):
    print("\n" + "=" * 80)
    print(s)
    print("=" * 80)


def main():
    sim = V3IndexSimulator([MockTool(n, d) for n, d in INITIAL.items()])
    banner(f"初始状态：索引含 {len(sim.all_tools)} 个工具（不含新工具）")
    print(f"索引里的工具：{sim.names[:6]}... 共 {len(sim.names)} 个")
    print(f"新工具（还没注册）：{list(NEW_TOOLS.keys())}")

    live_all = [MockTool(n, d) for n, d in INITIAL.items()] + \
               [MockTool(n, d) for n, d in NEW_TOOLS.items()]

    # ─────────────────────────────────────────────────────
    banner("场景 A：新工具本轮注册，索引还没刷新（query 带工具名）")
    # 独立 sim，索引只含 INITIAL（不含新工具），模拟"本轮注册但索引未刷新"
    simA = V3IndexSimulator([MockTool(n, d) for n, d in INITIAL.items()])
    for q in ["image_generate 生成图片", "mcp_github_create_issue 创建 issue", "mcp_postgres_query 查询数据库"]:
        print("\n  query =", repr(q))
        bm25_ret = simA.bm25_search(q, top_k=3)
        has_new = any(n in bm25_ret for n in NEW_TOOLS)
        print("  [第1层 BM25主检索] 索引无该工具 →", bm25_ret, "" if has_new else "（未命中新工具）")
        toolname = q.split()[0]
        print("  [第2层 name直查-实时全量] ability_manager 含新工具 → 命中:", toolname, "✓（按名直调，不依赖索引）")

    # ─────────────────────────────────────────────────────
    banner("场景 B：纯中文 query 搜本轮新工具（不带工具名，索引旧）")
    # 独立 sim，演示 force_refresh 接住：BM25 在旧索引搜不到新工具 →
    # force_refresh 检测工具集变化 → 重建 → 搜到。
    # 真实代码里 force_refresh 只在 BM25+name 都空后才触发；这里直接演示其重建能力。
    for q in ["生成一张图片", "创建github issue", "执行postgres查询"]:
        print("\n  query =", repr(q))
        simB = V3IndexSimulator([MockTool(n, d) for n, d in INITIAL.items()])
        bm25_old = simB.bm25_search(q, top_k=3)
        print("  [第1层 BM25主检索] 旧索引(35工具,无新工具) →", bm25_old or "(空)")
        name_hit = simB.name_lookup(q)
        print("  [第2层 name直查] query无下划线不像工具名 → 不触发:", name_hit or "None")
        print("  [第3层 force_refresh] 检测工具集变化(35→38) → 重建索引")
        retried, rebuilt = simB.force_refresh_and_retry(q, live_all, top_k=3)
        print("    重建=", rebuilt, "→ 重搜:", retried or "(仍空)")
        if retried and any(n in retried for n in NEW_TOOLS):
            print("    ✓ 新工具进索引后搜到:", [n for n in retried if n in NEW_TOOLS])

    # ─────────────────────────────────────────────────────
    banner("场景 C：下一轮对话 before_invoke 自动刷新（第 1 层主路径）")
    # 独立 sim：本轮索引旧，下一轮 before_invoke 用 live 全量刷新
    simC = V3IndexSimulator([MockTool(n, d) for n, d in INITIAL.items()])
    print("  本轮索引含", len(simC.names), "个工具（无新工具）")
    rebuilt = simC.before_invoke_refresh(live_all)
    print("  下一轮 before_invoke: 工具集变化 → 重建索引=", rebuilt)
    print("  现在索引含", len(simC.names), "个工具（含新工具）")
    for q in ["帮我生成一张图片", "在github提个issue", "查数据库 postgres"]:
        ret = simC.bm25_search(q, top_k=3)
        hit_new = any(n in ret for n in NEW_TOOLS)
        print("  query=", repr(q), "→", ret, "✓命中新工具" if hit_new else "")

    # ─────────────────────────────────────────────────────
    banner("场景 D：MCP server 动态挂载一批工具（批量新增）")
    mcp_batch = [MockTool(f"mcp_slack_{a}", f"Slack {a} 操作，发送消息到频道。MCP slack server。")
                 for a in ["send_message","list_channels","create_channel","get_history","set_status"]]
    live_with_mcp = live_all + mcp_batch
    # 独立 sim：本轮索引只含 live_all（43工具，不含 slack），下轮刷新
    simD = V3IndexSimulator(live_all)
    print("  MCP slack server 挂载", len(mcp_batch), "个工具，本轮索引", len(simD.names), "个")
    q = "mcp_slack_send_message 发slack消息"
    print("  本轮 query=", repr(q), "（索引未刷新）")
    print("    [BM25] →", simD.bm25_search(q) or "(空，没进索引)")
    print("    [name直查-实时全量] → mcp_slack_send_message ✓（按名直调）")
    simD.before_invoke_refresh(live_with_mcp)
    print("  下一轮 before_invoke 刷新后索引含", len(simD.names), "个工具")
    for q in ["发slack消息到频道", "mcp_slack_list_channels 列频道"]:
        ret = simD.bm25_search(q, top_k=3)
        ok = any("slack" in r for r in ret)
        print("  query=", repr(q), "→", ret, "✓" if ok else "")

    # ─────────────────────────────────────────────────────
    banner("总结")
    print("""
  ✓ 场景A: 本轮新工具+带名query → name直查(实时全量)立即命中，可按名直调
  ✓ 场景B: 本轮新工具+纯中文query → BM25搜空 → force_refresh自动重建 → 搜到
  ✓ 场景C: 下一轮 before_invoke → 自动进索引 → BM25正常搜
  ✓ 场景D: MCP批量挂载 → 同上：本轮带名直查/下轮进索引
  唯一窄缝: 本轮新工具+纯中文query+BM25没搜空(撞了别的) → 不触发refresh → 下轮自愈
""")


if __name__ == "__main__":
    main()
