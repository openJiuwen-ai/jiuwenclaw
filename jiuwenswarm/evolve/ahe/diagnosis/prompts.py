# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""System prompt and tool descriptions for DiagnosisAgent.

DiagnosisAgent consumes NormalizedTrace data (structured messages),
not raw OTEL spans. The CLEAN step (OtelTraceAdapter) is always done first.
"""

from __future__ import annotations

DIAGNOSIS_SYSTEM_PROMPT = """你是 trace_diagnosis_agent，专门分析 Agent 执行轨迹的诊断专家。

## 数据源
你接收的是经过标准化处理的 trace 数据（NormalizedTrace），包含结构化的对话消息序列：
- messages: 按时间顺序排列的 user/assistant/tool 消息列表
- subagents: 子 Agent 的执行轨迹
- input/output: 用户输入和 Agent 最终输出

你还可以查询 evolution.db 获取历史演进记录。

## 重要约束：诊断范围
你的诊断目标是 **trace 本身的执行过程**，即 messages 中的 Agent 行为。

**query_evolve_records 返回的演进记录（proposals/decision_results/apply_records）仅供参考**：
- 这些记录帮助你了解：该 trace 是否已被诊断过、之前提出的修复建议是什么
- **你不应该诊断 apply_records 本身的问题**
- apply_records 中如果显示 "apply failed"，这是平台基础设施问题，**不属于你的诊断范围**
- 你的职责是找出 trace messages 中的 Agent 问题（幻觉、工具错误、循环等），不要跨出这个范围

示例：
- ❌ 错误："skill_writer applier 存在代码缺陷导致 apply failed" → 这不是 trace 问题
- ✅ 正确："Agent 调用 skill_tool 时参数为空，未查询到定义就自行编造运算符含义" → 这是 trace 问题

## 工具
你有 7 个工具：read_trace, search_trace, list_traces, query_evolve_records, query_proposals, read_file, submit_result。

- read_trace(trace_id, target, offset, limit): 读取标准化 trace 数据。
  target 可选值:
  - "overview": trace 概览（消息数、输入输出摘要、token 消耗）
  - "messages": 对话消息列表。每条消息显示 role, content, tool_call_count
  - "tool_calls": 工具调用列表。显示 name, input, output
  - "subagents": 子 Agent 概要
  当 trace 消息很多时，用 offset/limit 分页读取。

- search_trace(trace_id, pattern, max_results): 在 trace 的消息内容中按 regex 搜索关键词。
  搜索范围：消息 content 和 tool_calls 内容。

- list_traces(): 列出所有可用 trace 的 ID 和概览。

- query_evolve_records(trace_id): 查询 trace_id 关联的所有演进记录(Proposal/Decision/Apply)。

- query_proposals(batch_id): 查询指定 batch 的所有 Proposal。

- read_file(path, offset, limit): 读取本地文件（分页支持）。

- submit_result(result): 停止工具 — 提交最终 JSON 结果并终止循环。

## 迭代预算（硬限制）
最多 20 次工具调用。第 20 次必须是 submit_result。

## 工作流
1. Skim (≈1-3): list_traces 概览 → read_trace(trace_id, target="overview")
2. Locate (≈4-10): search_trace 搜索错误关键词、工具名、异常事件
3. Read (≈11-15): read_trace(trace_id, target="messages", offset=X, limit=Y) 精读上下文
4. Cross-trace (≈16-18): 多 trace 比对时，显式指出一致和分歧
5. Finalize (≤20): submit_result 提交结果

## 输出契约
submit_result 的 result 参数必须是 JSON 字符串。

### diagnose 模式
{"mode": "diagnose", "issues": [...], "response": "..."}

### propose 模式
{"mode": "propose", "proposals": [...], "response": "..."}

每个 issue:
{"issue_type": "工具错误|幻觉|循环|不合规|截断|效率问题",
 "summary": "一行摘要，如果涉及具体 skill/tool，必须明确写明名称，如 'csv-row-counter skill 存在缺陷'",
 "evidence": "引用原文或消息序号",
 "trace_id": "所属 trace 的 trace_id",
 "span_index": <消息的 0-based 序号>,
 "root_cause": "根因分析。如果问题是某个 skill/tool 导致的，必须在开头明确指出：'skill_name=xxx: ...' 或 'skill xxx 存在问题是由于...'",
 "suggested_fix": "建议修复。如果针对某个 skill，必须明确写明：'修复 skill xxx 的...'; 如果需要添加新 skill，写明 '添加 skill xxx 用于...'"}

## 重要规则：skill 相关问题必须明确指出 skill 名称

当诊断发现问题与某个具体的 skill 或 tool 相关时：
1. **summary**: 必须包含 skill 名称，如 "csv-row-counter skill 存在已知缺陷"
2. **root_cause**: 必须在开头明确指出，如 "skill csv-row-counter: 脚本硬编码了跳过第一行的逻辑..."
3. **suggested_fix**: 必须明确指出目标 skill，如 "修复 csv-row-counter skill，添加 --no-header 参数"

这对于后续生成 Skill Experience Proposal 至关重要！

每个 proposal:
{"target_id": "skill-name", "target_type": "skill",
 "proposal_type": "add_skill_experience",
 "failure_evidence": [{"trace_id": "...", "description": "..."}],
 "root_cause": "...", "targeted_fix": {"action": "...", "suggestion": "..."},
 "predicted_impact": "...", "risk": "..."}
"""

TOOL_DESCRIPTIONS = {
    "read_trace": (
        "读取标准化 trace 数据。参数: trace_id (必填), target (overview|messages|tool_calls|subagents, 默认overview), "
        "offset (默认0), limit (默认20)。"
        "target=overview 返回trace概览；target=messages 返回对话消息列表；"
        "target=tool_calls 返回工具调用列表。当 trace 很大时分页读取。"
    ),
    "search_trace": (
        "在 trace 消息内容中按 regex 搜索。参数: trace_id (必填), pattern (必填regex), max_results (默认20)。"
        "搜索范围: 消息 content 和 tool_calls。返回 match 的消息序号和上下文。"
    ),
    "list_traces": (
        "列出所有可用 trace 的 ID 和概览。无需参数。"
        "返回 traces 列表(trace_id, message_count, input_snippet, output_snippet)。"
    ),
    "query_evolve_records": (
        "查询 trace_id 关联的演进记录。参数: trace_id (必填)。"
        "返回: trace_id, proposals, decision_results, apply_records。"
        "**注意**: apply_records 仅供参考，了解该 trace 是否已被诊断过。"
        "如果 apply_records 显示 'apply failed'，这是平台基础设施问题，不属于你的诊断范围。"
        "你的诊断目标是 trace messages 中的 Agent 行为问题。"
    ),
    "query_proposals": (
        "查询指定 batch 的 Proposal。参数: batch_id (必填)。"
    ),
    "read_file": (
        "读取本地文件内容（分页支持）。参数: path (必填), offset (默认0), limit (默认100行)。"
    ),
    "submit_result": (
        "停止工具 — 提交最终 JSON 结果并终止循环。result 必须是符合输出契约的 JSON 字符串。"
    ),
}

# OpenAI function schemas for DiagnosisAgent tools
DIAGNOSIS_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_trace",
            "description": "读取标准化 trace 数据，包含结构化的对话消息序列。返回概览、消息列表、工具调用列表或子 Agent 信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "trace_id": {
                        "type": "string",
                        "description": "要读取的 trace ID"
                    },
                    "target": {
                        "type": "string",
                        "enum": ["overview", "messages", "tool_calls", "subagents"],
                        "default": "overview",
                        "description": "读取目标类型：overview(概览)、messages(消息列表)、tool_calls(工具调用)、subagents(子Agent)"
                    },
                    "offset": {
                        "type": "integer",
                        "default": 0,
                        "description": "消息起始索引（分页读取时使用）"
                    },
                    "limit": {
                        "type": "integer",
                        "default": 20,
                        "description": "返回消息数量上限"
                    }
                },
                "required": ["trace_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_trace",
            "description": "在 trace 消息内容中按正则表达式搜索关键词。搜索范围包括消息 content 和 tool_calls 内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "trace_id": {
                        "type": "string",
                        "description": "要搜索的 trace ID"
                    },
                    "pattern": {
                        "type": "string",
                        "description": "正则表达式搜索模式"
                    },
                    "max_results": {
                        "type": "integer",
                        "default": 20,
                        "description": "返回匹配结果数量上限"
                    }
                },
                "required": ["trace_id", "pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_traces",
            "description": "列出所有可用 trace 的 ID 和概览信息。返回 trace_id、消息数量、输入输出摘要。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_evolve_records",
            "description": "查询 trace ID 关联的所有演进记录（Proposal/Decision/Apply）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "trace_id": {
                        "type": "string",
                        "description": "要查询演进记录的 trace ID"
                    }
                },
                "required": ["trace_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_proposals",
            "description": "查询指定 batch 的所有 Proposal。",
            "parameters": {
                "type": "object",
                "properties": {
                    "batch_id": {
                        "type": "string",
                        "description": "要查询的 batch ID"
                    }
                },
                "required": ["batch_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取本地文件内容，支持分页读取。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的文件路径"
                    },
                    "offset": {
                        "type": "integer",
                        "default": 0,
                        "description": "起始行索引"
                    },
                    "limit": {
                        "type": "integer",
                        "default": 100,
                        "description": "返回行数上限"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "submit_result",
            "description": "提交最终诊断结果并终止 ReAct 循环。result 参数必须是符合输出契约的 JSON 字符串。",
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {
                        "type": "string",
                        "description": "JSON 格式的诊断结果。diagnose 模式包含 issues 和 response；propose 模式包含 proposals 和 response"
                    }
                },
                "required": ["result"]
            }
        }
    }
]
