#!/usr/bin/env python3
"""分析 prompt_capture 数据，输出全链路工具使用报告。

用法: python analyze_capture.py <session_id 或 jsonl 文件路径>
示例: python analyze_capture.py sess_19f4aa78704_594b75
"""

import json
import os
import sys
from collections import Counter
from pathlib import Path

# Fix Windows GBK encoding
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def get_capture_dir() -> Path:
    base = Path.home() / ".jiuwenswarm" / "agent" / ".logs" / "prompt_capture"
    if not base.exists():
        base = Path.home() / ".jiuwenswarm" / "logs" / "prompt_capture"
    return base


def find_session_file(session_id: str) -> Path | None:
    base = get_capture_dir()
    # 先精确匹配
    for f in base.glob(f"*{session_id}*.jsonl"):
        return f
    # 如果传入的是文件路径
    p = Path(session_id)
    if p.exists():
        return p
    return None


def analyze(jsonl_path: Path, snapshot_path: Path | None = None) -> dict:
    rounds: list[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rounds.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # 按文件顺序配对 input 与其对应的 LLM_OUTPUT。
    # 注意：call_index 在多用户轮次 session 里会重置，不能用 dict 按 call_index 去重
    # （会把多轮的同 call_index output 压成一条）。capture 是 input→output 交替写入，
    # 按文件顺序配对即可还原每轮真实对应关系。
    pairs: list[tuple] = []  # (input_rec | None, output_rec | None)
    pending_input = None
    for r in rounds:
        if r.get("type") == "llm_output":
            if pending_input is not None:
                pairs.append((pending_input, r))
                pending_input = None
            else:
                pairs.append((None, r))  # 孤儿 output（无前导 input）
        else:
            if pending_input is not None:
                pairs.append((pending_input, None))  # 上一条 input 没有 output
            pending_input = r
    if pending_input is not None:
        pairs.append((pending_input, None))

    inputs = [p[0] for p in pairs if p[0] is not None]
    outputs_paired = [p[1] for p in pairs if p[1] is not None]
    input_pairs = [(p[0], p[1]) for p in pairs if p[0] is not None]

    def get_called_names(output_rec) -> list[str]:
        """从配对的 LLM_OUTPUT.tool_calls_made 提取本轮真实调用。

        不再读 messages 历史（旧实现按最后一条 assistant tool_calls 提取，
        会滞后一轮，造成 off-by-one）。
        """
        if not output_rec:
            return []
        tcs = output_rec.get("tool_calls_made") or []
        names = []
        for tc in tcs:
            nm = tc.get("name", "")
            if nm:
                names.append(nm)
        return names

    # ---- 注册表 ----
    registered: list[str] = []
    if snapshot_path and snapshot_path.exists():
        snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
        registered = snap.get("registered_tool_names", [])
        reg_count = snap.get("registered_tool_count", len(registered))
    else:
        # 尝试从 capture 目录找
        snap_file = get_capture_dir() / "_ability_snapshot.json"
        if snap_file.exists():
            snap = json.loads(snap_file.read_text(encoding="utf-8"))
            registered = snap.get("registered_tool_names", [])
            reg_count = snap.get("registered_tool_count", len(registered))
        else:
            reg_count = 0

    # ---- 逐轮分析 ----
    injected_history: list[set[str]] = []
    called_history: list[set[str]] = []
    all_calls: Counter = Counter()

    print("=" * 70)
    print("全链路工具追踪报告")
    print(f"文件: {jsonl_path.name}")
    print(f"总 LLM 调用轮次: {len(inputs)} (input), {len(outputs_paired)} (output)")
    print("=" * 70)

    if reg_count:
        print(f"\n📦 注册表 (ability_manager): {reg_count} 个工具")
        print("   " + ", ".join(registered))

    print(f"\n{'='*70}")
    print(f"{'轮次':<6} {'注入数':<7} {'调用数':<7} {'注入的工具':<46} {'LLM调用了'}")
    print(f"{'='*70}")

    prev_ci = 0
    turn = 0
    for idx, (rec, out) in enumerate(input_pairs):
        ci = rec.get("call_index", idx + 1)
        tc = rec.get("tool_count", 0)
        tools_in = sorted([t.get("function", {}).get("name", "?")
                          for t in rec.get("tools", [])])
        injected_history.append(set(tools_in))

        call_names = get_called_names(out)
        called_history.append(set(call_names))
        for cn in call_names:
            all_calls[cn] += 1

        # call_index 回退/重置视为新用户轮次
        if idx == 0 or ci <= prev_ci:
            turn += 1
            if idx > 0:
                print("-" * 70)
            print(f"── 第 {turn} 个用户轮次 ──")
        prev_ci = ci

        tools_str = ", ".join(tools_in)
        calls_str = ", ".join(call_names) if call_names else "(纯文本回复)"

        # 截断过长的工具列表
        if len(tools_str) > 44:
            tools_str = f"[{tc}个] " + ", ".join(tools_in[:3]) + "..."

        print(f"{ci:<6} {tc:<7} {len(call_names):<7} {tools_str:<46} {calls_str}")

    # ---- 汇总 ----
    print(f"\n{'='*70}")
    print("汇总")
    print(f"{'='*70}")

    # 注入变化
    if injected_history:
        first_injected = injected_history[0]
        last_injected = injected_history[-1]
        all_injected_same = all(s == first_injected for s in injected_history)
        print(f"\n🔧 注入的 tools 参数:")
        if all_injected_same:
            print(f"  始终 {len(first_injected)} 个，从第一轮到最后一轮完全一致")
            print(f"  元工具: {', '.join(sorted(first_injected))}")
        else:
            print(f"  第1轮: {len(first_injected)} 个")
            print(f"  最后一轮: {len(last_injected)} 个")
            added = last_injected - first_injected
            removed = first_injected - last_injected
            if added:
                print(f"  增长: +{len(added)} = {', '.join(sorted(added))}")
            if removed:
                print(f"  减少: -{len(removed)} = {', '.join(sorted(removed))}")

    # 调用统计
    print(f"\n📞 LLM 调用的工具 (按频次排序):")
    if all_calls:
        for name, count in all_calls.most_common():
            in_tools = "✅" if any(name in s for s in injected_history) else "💭(凭记忆)"
            print(f"  {name:<35} {count:>3} 次  {in_tools}")
    else:
        print("  (无 — 可能 LLM_OUTPUT 未捕获，需要重新运行测试)")

    # 凭记忆调用 vs 注入调用
    memory_calls = {name: count for name, count in all_calls.items()
                    if not any(name in s for s in injected_history)}
    injected_calls = {name: count for name, count in all_calls.items()
                      if any(name in s for s in injected_history)}
    if memory_calls:
        print(f"\n💭 凭记忆调用 ({len(memory_calls)} 个工具，不在 tools 参数中):")
        for name, count in sorted(memory_calls.items(), key=lambda x: -x[1]):
            print(f"  {name}: {count} 次")
    if injected_calls:
        print(f"\n✅ 注入调用 ({len(injected_calls)} 个工具，在 tools 参数中):")
        for name, count in sorted(injected_calls.items(), key=lambda x: -x[1]):
            print(f"  {name}: {count} 次")

    # 首现分析
    print(f"\n🆕 工具首次出现轮次:")
    seen = set()
    for i, calls in enumerate(called_history):
        new = calls - seen
        if new:
            print(f"  轮次{i+1}: 首次调用 {', '.join(sorted(new))}")
            seen |= new

    print()
    return {
        "registered_count": reg_count,
        "injected_always_same": all_injected_same,
        "injected_count_first": len(injected_history[0]) if injected_history else 0,
        "injected_count_last": len(injected_history[-1]) if injected_history else 0,
        "total_calls": sum(all_calls.values()),
        "memory_calls": len(memory_calls),
        "injected_calls": len(injected_calls),
        "unique_tools_called": len(all_calls),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python analyze_capture.py <session_id 或文件路径>")
        print(f"可用 session 文件:")
        base = get_capture_dir()
        if base.exists():
            for f in sorted(base.glob("sess_*.jsonl")):
                print(f"  {f.name}")
        sys.exit(1)

    target = sys.argv[1]
    fpath = find_session_file(target)
    if fpath is None:
        print(f"找不到 session 文件: {target}")
        sys.exit(1)

    analyze(fpath)
