#!/usr/bin/env python3
"""将 prompt_capture 的 JSONL 转为可读的 TXT 文件。"""

import json
import sys
from pathlib import Path

def fmt_tools(tools: list) -> str:
    lines = []
    for t in tools:
        name = t.get("function", t).get("name", "?")
        desc = t.get("function", t).get("description", "")
        if desc:
            lines.append(f"  - {name}: {desc[:120]}...")
        else:
            lines.append(f"  - {name}")
    return "\n".join(lines)

def fmt_messages(messages: list) -> str:
    parts = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, str):
            text = content[:500]
        elif isinstance(content, list):
            text = json.dumps(content, ensure_ascii=False)[:500]
        else:
            text = str(content)[:500]
        parts.append(f"\n--- {role.upper()} ({len(text)} chars) ---\n{text}")
    return "\n".join(parts)

def convert(jsonl_path: Path) -> str:
    records = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    output = []
    for r in records:
        output.append(f"\n{'='*60}")
        output.append(f"LLM Call #{r.get('call_index', '?')}")
        output.append(f"  Model: {r.get('model_name', '?')}")
        output.append(f"  Session: {r.get('session_id', '?')}")
        output.append(f"  Request: {r.get('request_id', '?')}")
        output.append(f"  Query: {r.get('user_query', '?')[:200]}")
        output.append(f"  Messages: {r.get('message_count', '?')} | Tools: {r.get('tool_count', '?')}")

        output.append(f"\n--- System Messages ({len(r.get('system_messages', []))}) ---")
        for sm in r.get("system_messages", []):
            output.append(sm[:2000])

        output.append(f"\n--- Conversation History ({r.get('message_count', 0)} messages) ---")
        output.append(fmt_messages(r.get("messages", [])))

        output.append(f"\n--- Tools ({r.get('tool_count', 0)}) ---")
        output.append(fmt_tools(r.get("tools", [])))

    return "\n".join(output)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        prompt_dir = Path.home() / ".jiuwenswarm" / "agent" / ".logs" / "prompt_capture"
        jsonl_files = list(prompt_dir.glob("*.jsonl"))
        if not jsonl_files:
            print(f"未找到 .jsonl 文件，请指定路径: python {sys.argv[0]} <file.jsonl>")
            sys.exit(1)
        jsonl_path = max(jsonl_files, key=lambda p: p.stat().st_mtime)
        print(f"使用最新的文件: {jsonl_path}")
    else:
        jsonl_path = Path(sys.argv[1])

    output_path = jsonl_path.with_suffix(".txt")
    result = convert(jsonl_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(result)

    print(f"已转换: {jsonl_path} → {output_path}")
    print(f"大小: {len(result)} 字符")
